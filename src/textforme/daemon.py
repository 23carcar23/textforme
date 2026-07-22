"""textformed — background service. Owner: Agent 2.

Wires ImsgClient + Database + AnthropicResponder + policies into the pipeline
of ARCHITECTURE §6, and serves the Unix-socket control protocol of §5.

Structure:
- Daemon class holding components; dependency-injectable for tests
  (imsg_client, database, responder_factory).
- watch loop: async for msg in imsg.watch_messages(since_rowid=settings.last_seen_rowid)
  → process_message(msg) (spawned as a task; a per-chat lock keeps replies to
  one chat ordered) → advance last_seen_rowid watermark after each event.
- process_message gates each message through the policy layer, then either
  replies immediately or, when the contact has the reply timer enabled,
  accumulates the message behind a random 0–3 minute countdown so a burst
  gets one batched reply (_fire_reply). Outcomes are recorded in the DB.
  The responder is (re)built lazily from keychain + settings so a replaced key
  or model takes effect without restart.
- Socket server: asyncio.start_unix_server at config.SOCKET_PATH (chmod 0600,
  stale socket file removed on bind); JSON-lines request/response, one client
  request at a time per connection.
- Contact sync: on startup and on contacts.refresh, chats.list → Database.upsert_contact
  (never touching ai_enabled of existing rows).
- Signals: SIGTERM/SIGINT → graceful shutdown (stop imsg, close DB, remove socket).
- Logging: rotating file in config.LOG_DIR/daemon.log; NEVER log message bodies,
  reply text, or the API key — GUIDs, chat ids, statuses, error codes only.

main() is the `textformed` console entry point.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import random
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config, contact_names, keychain
from .anthropic.client import AnthropicClient
from .database import ContactRecord, Database
from .messaging.client import ImsgClient
from .messaging.events import (
    AnthropicUnavailableError,
    ErrorCode,
    ImsgError,
    ReplyValidationError,
    SkipReason,
)
from .messaging.models import Message
from .service import policies
from .service.briefer import generate_brief
from .service.responder import AnthropicResponder
from .service.scheduler import ChatLocks, ReplyTimerManager

logger = logging.getLogger("textformed")

_HEALTH_CHECK_INTERVAL = 30.0
_SYNC_RETRY_INTERVAL = 30.0
_MODELS_CACHE_TTL = 600.0


class _SocketError(Exception):
    """Internal control-flow error mapped to a socket protocol error response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Daemon:
    def __init__(
        self,
        *,
        imsg_client: ImsgClient | None = None,
        database: Database | None = None,
        responder: AnthropicResponder | None = None,
        api_key_getter: Callable[[], str | None] | None = None,
        anthropic_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.imsg: ImsgClient = imsg_client if imsg_client is not None else ImsgClient()
        self.database: Database | None = database
        self._responder_override = responder
        self.api_key_getter: Callable[[], str | None] = api_key_getter or keychain.get_api_key
        self._anthropic_factory: Callable[[str], Any] = anthropic_client_factory or AnthropicClient

        self._models_cache: list[dict[str, str]] | None = None
        self._models_cache_time = 0.0

        # Per-chat locks serialize replies to one chat (in order, nothing
        # dropped); the timer manager holds the realistic-texting batch
        # countdowns for contacts with the reply timer enabled.
        self._chat_locks = ChatLocks()
        self._timers = ReplyTimerManager()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._server: asyncio.base_events.Server | None = None

        self._watermark_lock = asyncio.Lock()
        self._last_seen_rowid = 0

        self._last_imsg_health = False
        self._last_health_check_time = 0.0
        self._last_error: str | None = None

        self._shutdown_event = asyncio.Event()
        self._watch_task: asyncio.Task[Any] | None = None
        self._sync_retry_task: asyncio.Task[Any] | None = None

    # -- top-level lifecycle -----------------------------------------------------

    async def run(self) -> None:
        config.ensure_dirs()

        if self.database is None:
            self.database = Database(config.DB_PATH)

        settings = self.database.get_settings()
        self._last_seen_rowid = settings.last_seen_rowid

        await self.imsg.start()
        try:
            count = await self._sync_contacts()
            logger.info("startup contact sync ok (%d chats)", count)
        except ImsgError as exc:
            # Most commonly Full Disk Access not (yet) granted for the launchd
            # context. Keep running: serve the socket so the TUI can show
            # status, and retry in the background until access appears.
            self._last_error = f"contact sync failed: {exc}"
            logger.warning("initial contact sync failed (%s); daemon starting anyway", exc)
            self._sync_retry_task = asyncio.create_task(self._retry_initial_sync())
        await self._start_socket_server()
        logger.info("daemon ready (socket: %s)", config.SOCKET_PATH)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, self._shutdown_event.set)

        self._watch_task = asyncio.create_task(self._watch_loop())

        try:
            await self._shutdown_event.wait()
        finally:
            await self._shutdown()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        for task_attr in ("_watch_task", "_sync_retry_task"):
            task = getattr(self, task_attr)
            setattr(self, task_attr, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self._timers.cancel_all()

        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

        with contextlib.suppress(Exception):
            if config.SOCKET_PATH.exists():
                config.SOCKET_PATH.unlink()

        with contextlib.suppress(Exception):
            await self.imsg.stop()

        if self.database is not None:
            with contextlib.suppress(Exception):
                self.database.close()

    # -- contact sync --------------------------------------------------------------

    async def _sync_contacts(self) -> int:
        assert self.database is not None
        chats = await self.imsg.list_contacts()
        # Best-effort local Address Book fallback for chats where imsg had no
        # resolved name (e.g. Contacts permission not granted to `imsg rpc`).
        # Loaded once per sync; degrades to {} on any permission/I-O failure.
        name_map = contact_names.load_contact_names()
        for chat in chats:
            display_name = chat.display_name
            if not display_name and not chat.is_group:
                display_name = contact_names.resolve(chat.address, name_map) or ""
            self.database.upsert_contact(
                ContactRecord(
                    chat_guid=chat.guid,
                    chat_id=chat.chat_id,
                    display_name=display_name,
                    address=chat.address,
                    service=chat.service,
                    is_group=chat.is_group,
                    ai_enabled=False,
                    last_seen_message_guid=None,
                )
            )
        return len(chats)

    async def _retry_initial_sync(self) -> None:
        """Keep retrying the startup contact sync (e.g. until Full Disk Access
        is granted for the launchd context), then stop."""
        while True:
            await asyncio.sleep(_SYNC_RETRY_INTERVAL)
            try:
                count = await self._sync_contacts()
            except ImsgError as exc:
                self._last_error = f"contact sync failed: {exc}"
                continue
            logger.info("contact sync recovered (%d chats)", count)
            if (self._last_error or "").startswith("contact sync failed"):
                self._last_error = None
            return

    # -- watch loop ------------------------------------------------------------

    async def _watch_loop(self) -> None:
        try:
            async for msg in self.imsg.watch_messages(since_rowid=self._last_seen_rowid):
                task = asyncio.create_task(self.process_message(msg))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("watch loop terminated unexpectedly")

    async def _advance_watermark(self, rowid: int) -> None:
        assert self.database is not None
        async with self._watermark_lock:
            if rowid > self._last_seen_rowid:
                self._last_seen_rowid = rowid
                self.database.set_setting("last_seen_rowid", str(rowid))

    # -- message pipeline (ARCHITECTURE §6) -----------------------------------------

    async def process_message(self, msg: Message) -> None:
        try:
            await self._process_message_inner(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("unhandled error processing message rowid=%s", msg.rowid)
            self._last_error = "internal error processing message"
        finally:
            await self._advance_watermark(msg.rowid)

    async def _process_message_inner(self, msg: Message) -> None:
        assert self.database is not None
        database = self.database

        # Step 1: never react to our own outgoing messages.
        if msg.is_from_me:
            return

        # Step 2: reactions / empty text / attachment-only messages are ignored.
        if not msg.is_substantive:
            return

        # Step 3: dedup.
        if database.is_processed(msg.guid):
            return

        # Every substantive, non-duplicate inbound text is noted in the log,
        # regardless of whether AI is toggled on for this contact — the outcome
        # (skipped/failed/replied) is logged separately below.
        logger.info(
            "message received chat=%s rowid=%s guid=%s", msg.chat_id, msg.rowid, msg.guid
        )

        # Step 4: contact lookup, syncing from imsg if this chat_id is unknown.
        contact = database.get_contact_by_chat_id(msg.chat_id)
        if contact is None:
            await self._sync_contacts()
            contact = database.get_contact_by_chat_id(msg.chat_id)

        # Steps 5-12: policy gate. Without cooldown/rate-limit there is no
        # need for a second post-delay re-evaluation — this decides it.
        settings = database.get_settings()
        decision = policies.evaluate(self._policy_inputs(contact, settings))

        if not decision.allowed:
            if decision.trigger_auto_pause:
                database.set_setting("paused", "true")
            chat_guid_for_record = contact.chat_guid if contact is not None else f"unknown:{msg.chat_id}"
            reason = decision.skip_reason or SkipReason.UNKNOWN_CONTACT
            self._record_skip(msg, chat_guid_for_record, reason)
            return

        # `allowed` implies a known, non-group, AI-enabled contact.
        assert contact is not None
        chat_guid = contact.chat_guid

        if contact.reply_timer_enabled:
            # Realistic-texting path: accumulate this message and let the
            # per-chat countdown fire one batched reply. The first message of a
            # burst starts the timer; later ones only get saved.
            database.record_processed(msg.guid, chat_guid, "batched")
            self._timers.add_message(chat_guid, msg)
            if not self._timers.is_running(chat_guid):
                delay = random.uniform(0.0, config.REPLY_TIMER_MAX_SECONDS)
                self._timers.start(chat_guid, delay, self._fire_reply)
                logger.info(
                    "message guid=%s chat=%s batched (reply timer started, %.0fs)",
                    msg.guid, chat_guid, delay,
                )
            else:
                logger.info("message guid=%s chat=%s batched (reply timer running)", msg.guid, chat_guid)
            return

        # Immediate path: reply to this message on its own. The per-chat lock
        # keeps replies ordered without dropping any of them.
        async with self._chat_locks.lock(chat_guid):
            await self._reply_now(msg, contact, settings)

    async def _fire_reply(self, chat_guid: str) -> None:
        """Countdown expired for a batched chat: send one reply covering the
        whole burst that accumulated during the window."""
        assert self.database is not None
        database = self.database

        batch = self._timers.collect(chat_guid)
        if not batch:
            return
        # The full conversation (loaded below) already contains every batched
        # message; the last one stands in as the "incoming" turn.
        last_msg = batch[-1]

        contact = database.get_contact(chat_guid)
        settings = database.get_settings()
        decision = policies.evaluate(self._policy_inputs(contact, settings))
        if not decision.allowed:
            if decision.trigger_auto_pause:
                database.set_setting("paused", "true")
            self._record_skip(last_msg, chat_guid, decision.skip_reason or SkipReason.UNKNOWN_CONTACT)
            return
        assert contact is not None

        async with self._chat_locks.lock(chat_guid):
            await self._reply_now(last_msg, contact, settings)

    async def _reply_now(
        self, msg: Message, contact: ContactRecord, settings: config.Settings
    ) -> None:
        """Load context, generate, send, and record one reply for ``msg``.

        Shared by the immediate and batched paths; callers hold the chat lock.
        """
        assert self.database is not None
        database = self.database

        # Load conversation context.
        try:
            history = await self.imsg.get_history(contact.chat_id, settings.context_message_limit)
        except ImsgError as exc:
            self._record_failed(msg, contact, ErrorCode.IMSG_UNAVAILABLE, str(exc))
            return

        # Generate + validate the reply.
        responder = self._get_responder()
        if responder is None:
            self._record_failed(msg, contact, ErrorCode.NO_API_KEY, "no Anthropic API key configured")
            return
        if not settings.selected_model_id:
            self._record_failed(msg, contact, ErrorCode.NO_MODEL, "no model selected")
            return

        try:
            reply_text = await responder.generate_reply(
                contact,
                history,
                msg,
                settings.selected_model_id,
                config.MAX_REPLY_CHARS,
                system_prompt=settings.system_prompt,
                persona_prompt=settings.persona_prompt,
                style_profile=settings.style_profile,
            )
        except AnthropicUnavailableError as exc:
            detail = str(exc)
            code = ErrorCode.ANTHROPIC_TIMEOUT if "timeout" in detail.lower() else ErrorCode.ANTHROPIC_ERROR
            self._record_failed(msg, contact, code, detail)
            return
        except ReplyValidationError as exc:
            self._record_failed(msg, contact, ErrorCode.VALIDATION_FAILED, str(exc))
            return

        # Send.
        try:
            await self.imsg.send_message(contact.chat_id, reply_text)
        except ImsgError as exc:
            self._record_failed(msg, contact, ErrorCode.SEND_FAILED, str(exc))
            return

        # Record success.
        database.record_processed(msg.guid, contact.chat_guid, "replied", reply_sent=True)
        database.set_contact_last_seen(contact.chat_guid, msg.guid)
        logger.info("message guid=%s chat=%s replied", msg.guid, contact.chat_guid)
        self._last_error = None

    def _policy_inputs(
        self,
        contact: ContactRecord | None,
        settings: config.Settings,
    ) -> policies.PolicyInputs:
        """Assemble a fresh policy snapshot for the authorization decision."""
        assert self.database is not None
        return policies.PolicyInputs(
            contact=contact,
            settings=settings,
            consecutive_failures=self.database.recent_consecutive_failures(),
        )

    def _record_skip(self, msg: Message, chat_guid: str, reason: SkipReason) -> None:
        assert self.database is not None
        self.database.record_processed(msg.guid, chat_guid, f"skipped:{reason}")
        logger.info("message guid=%s chat=%s skipped reason=%s", msg.guid, chat_guid, reason)

    def _record_failed(self, msg: Message, contact: ContactRecord, code: ErrorCode, detail: str) -> None:
        assert self.database is not None
        self._last_error = f"{code}: {detail}" if detail else str(code)
        self.database.record_processed(msg.guid, contact.chat_guid, "failed", error_code=str(code))
        logger.info("message guid=%s chat=%s failed code=%s", msg.guid, contact.chat_guid, code)

    def _get_responder(self) -> AnthropicResponder | None:
        if self._responder_override is not None:
            return self._responder_override
        api_key = self.api_key_getter()
        if not api_key:
            return None
        client = AnthropicClient(api_key)
        return AnthropicResponder(client)

    # -- unix socket control protocol (ARCHITECTURE §5) ------------------------------

    async def _start_socket_server(self) -> None:
        socket_path = config.SOCKET_PATH
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(socket_path))
        with contextlib.suppress(Exception):
            os.chmod(socket_path, 0o600)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                response = await self._handle_request_line(line)
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _handle_request_line(self, line: bytes) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            return {"id": None, "ok": False, "error": {"code": "BAD_PARAMS", "message": "invalid JSON"}}

        if not isinstance(request, dict):
            return {"id": None, "ok": False, "error": {"code": "BAD_PARAMS", "message": "malformed request"}}

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return {"id": req_id, "ok": False, "error": {"code": "BAD_PARAMS", "message": "malformed request"}}

        try:
            result = await self._dispatch(method, params)
            return {"id": req_id, "ok": True, "result": result}
        except _SocketError as exc:
            return {"id": req_id, "ok": False, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:
            logger.exception("internal error handling socket method=%s", method)
            return {"id": req_id, "ok": False, "error": {"code": "INTERNAL", "message": str(exc)}}

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.database is not None
        database = self.database

        if method == "ping":
            return {}

        if method == "status":
            return await self._status()

        if method == "contacts.list":
            contacts = database.list_contacts()
            active = self._timers.active()
            payload = []
            for c in contacts:
                data = self._contact_to_dict(c)
                remaining = active.get(c.chat_guid)
                # Round up so a live timer never displays "0s" while ticking.
                data["reply_timer_remaining"] = (
                    int(remaining + 0.999) if remaining is not None else None
                )
                payload.append(data)
            return {"contacts": payload}

        if method == "contacts.set_ai":
            chat_guid = params.get("chat_guid")
            enabled = params.get("enabled")
            if not isinstance(chat_guid, str) or not isinstance(enabled, bool):
                raise _SocketError("BAD_PARAMS", "chat_guid (str) and enabled (bool) are required")
            try:
                database.set_contact_ai(chat_guid, enabled)
            except ValueError as exc:
                if str(exc) == "GROUP_FORBIDDEN":
                    raise _SocketError("GROUP_FORBIDDEN", "cannot toggle AI for group chats") from exc
                raise _SocketError("BAD_PARAMS", str(exc)) from exc
            except KeyError as exc:
                raise _SocketError("BAD_PARAMS", f"unknown contact: {chat_guid}") from exc
            return {}

        if method == "contacts.set_reply_timer":
            chat_guid = params.get("chat_guid")
            enabled = params.get("enabled")
            if not isinstance(chat_guid, str) or not isinstance(enabled, bool):
                raise _SocketError("BAD_PARAMS", "chat_guid (str) and enabled (bool) are required")
            try:
                database.set_contact_reply_timer(chat_guid, enabled)
            except ValueError as exc:
                if str(exc) == "GROUP_FORBIDDEN":
                    raise _SocketError("GROUP_FORBIDDEN", "cannot set a reply timer for group chats") from exc
                raise _SocketError("BAD_PARAMS", str(exc)) from exc
            except KeyError as exc:
                raise _SocketError("BAD_PARAMS", f"unknown contact: {chat_guid}") from exc
            # Turning the timer off leaves any running countdown to fire on its
            # own; the fire path re-checks policy, so this stays consistent.
            return {}

        if method == "contacts.set_description":
            chat_guid = params.get("chat_guid")
            description = params.get("description")
            if not isinstance(chat_guid, str) or not isinstance(description, str):
                raise _SocketError("BAD_PARAMS", "chat_guid (str) and description (str) are required")
            if len(description) > config.MAX_CONTACT_NOTE_CHARS:
                raise _SocketError(
                    "BAD_PARAMS",
                    f"description must be {config.MAX_CONTACT_NOTE_CHARS} characters or fewer",
                )
            try:
                database.set_contact_description(chat_guid, description)
            except KeyError as exc:
                raise _SocketError("BAD_PARAMS", f"unknown contact: {chat_guid}") from exc
            return {}

        if method == "contacts.refresh":
            count = await self._sync_contacts()
            return {"count": count}

        if method == "settings.get":
            return {"settings": database.get_raw_settings()}

        if method == "models.list":
            return await self._list_models()

        if method == "settings.set":
            key = params.get("key")
            value = params.get("value")
            if not isinstance(key, str) or value is None:
                raise _SocketError("BAD_PARAMS", "key (str) and value are required")
            # Reject values that would make Settings.from_mapping raise later,
            # which would degrade every subsequent pipeline run.
            candidate = database.get_raw_settings()
            if key in candidate:
                candidate[key] = str(value)
                try:
                    config.Settings.from_mapping(candidate)
                except (ValueError, TypeError) as exc:
                    raise _SocketError("BAD_PARAMS", f"invalid value for {key}") from exc
            try:
                database.set_setting(key, str(value))
            except KeyError as exc:
                raise _SocketError("UNKNOWN_KEY", f"unknown setting key: {key}") from exc
            return {}

        if method == "brief.generate":
            return await self._generate_brief()

        if method == "service.pause":
            database.set_setting("paused", "true")
            return {}

        if method == "service.resume":
            database.set_setting("paused", "false")
            return {}

        raise _SocketError("UNKNOWN_METHOD", f"unknown method: {method}")

    async def _generate_brief(self) -> dict[str, Any]:
        """Summarize the conversations the AI has replied to since the last
        brief. Returns {"status": "no_new"} when nothing new has happened,
        otherwise {"status": "ok", "summary": ..., "generated_at": ...}."""
        assert self.database is not None
        database = self.database

        raw = database.get_raw_settings()
        last_brief_at = raw.get("last_brief_at", "") or ""

        latest = database.latest_reply_at()
        if latest is None or (last_brief_at and latest <= last_brief_at):
            return {"status": "no_new"}

        chat_guids = database.chats_with_replies_since(last_brief_at)
        settings = database.get_settings()
        conversations: list[tuple[ContactRecord, list[Message]]] = []
        for chat_guid in chat_guids:
            contact = database.get_contact(chat_guid)
            if contact is None:
                continue
            try:
                history = await self.imsg.get_history(
                    contact.chat_id, settings.context_message_limit
                )
            except ImsgError:
                continue
            conversations.append((contact, history))

        if not conversations:
            return {"status": "no_new"}

        api_key = self.api_key_getter()
        if not api_key:
            raise _SocketError("NO_API_KEY", "no Anthropic API key configured")
        client = self._anthropic_factory(api_key)
        try:
            summary = await generate_brief(client, conversations)
        except AnthropicUnavailableError as exc:
            raise _SocketError("ANTHROPIC_UNAVAILABLE", str(exc)) from exc

        if not summary:
            return {"status": "no_new"}

        generated_at = datetime.now(timezone.utc).isoformat()
        # Watermark against the newest reply we actually summarized, so replies
        # that arrive mid-generation still count as "new" next time.
        database.set_setting("last_brief_at", latest)
        return {
            "status": "ok",
            "summary": summary,
            "generated_at": generated_at,
        }

    async def _list_models(self) -> dict[str, Any]:
        """Live model list for the TUI's model picker, cached briefly. The key
        never leaves the daemon — only ids and display names cross the socket."""
        now = time.monotonic()
        if self._models_cache is not None and now - self._models_cache_time < _MODELS_CACHE_TTL:
            return {"models": self._models_cache}
        api_key = self.api_key_getter()
        if not api_key:
            raise _SocketError("NO_API_KEY", "no Anthropic API key configured")
        client = self._anthropic_factory(api_key)
        try:
            models = await client.list_models()
        except AnthropicUnavailableError as exc:
            raise _SocketError("ANTHROPIC_UNAVAILABLE", str(exc)) from exc
        self._models_cache = [
            {"model_id": m.model_id, "display_name": m.display_name} for m in models
        ]
        self._models_cache_time = now
        return {"models": self._models_cache}

    @staticmethod
    def _contact_to_dict(contact: ContactRecord) -> dict[str, Any]:
        return {
            "chat_guid": contact.chat_guid,
            "chat_id": contact.chat_id,
            "display_name": contact.display_name,
            "address": contact.address,
            "service": contact.service,
            "is_group": contact.is_group,
            "ai_enabled": contact.ai_enabled,
            "description": contact.description,
            "reply_timer_enabled": contact.reply_timer_enabled,
        }

    async def _status(self) -> dict[str, Any]:
        assert self.database is not None
        settings = self.database.get_settings()

        now = time.monotonic()
        if now - self._last_health_check_time >= _HEALTH_CHECK_INTERVAL:
            self._last_imsg_health = await self.imsg.health_check()
            self._last_health_check_time = now

        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        replies_last_hour = self.database.replies_since(one_hour_ago)

        return {
            "running": True,
            "imsg_ok": self._last_imsg_health,
            "global_ai_enabled": settings.global_ai_enabled,
            "paused": settings.paused,
            "model_id": settings.selected_model_id,
            "replies_last_hour": replies_last_hour,
            "last_error": self._last_error,
        }


def _configure_logging() -> None:
    config.ensure_dirs()
    log_path = config.LOG_DIR / "daemon.log"
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(Daemon().run())
    except Exception:
        logger.exception("daemon crashed")
        raise
