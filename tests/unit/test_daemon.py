"""Unit tests for src/textforme/daemon.py (Agent 2 owns this).

Uses in-test fakes for ImsgClient, Database, and AnthropicResponder so these
tests never touch the real `imsg` binary, real SQLite, or the network. The
fake Database is dict-backed and implements the same public surface as
src/textforme/database.py's Database class, independent of its real
implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from textforme import config
from textforme import daemon as daemon_module
from textforme.daemon import Daemon
from textforme.database import ContactRecord
from textforme.messaging.events import AnthropicUnavailableError, ImsgUnavailableError, ReplyValidationError
from textforme.messaging.models import Message

from tests.fixtures.factories import make_contact, make_message


# -- fakes ----------------------------------------------------------------------


class FakeDatabase:
    """Dict-backed stand-in for textforme.database.Database's public surface."""

    def __init__(self) -> None:
        self._settings: dict[str, str] = dict(config.DEFAULT_SETTINGS)
        self._contacts: dict[str, ContactRecord] = {}
        self._processed: dict[str, dict] = {}
        self._seq = itertools.count(1)
        self.closed = False

    def close(self) -> None:
        self.closed = True

    # -- settings --
    def get_settings(self) -> config.Settings:
        return config.Settings.from_mapping(self._settings)

    def set_setting(self, key: str, value: str) -> None:
        if key not in config.DEFAULT_SETTINGS:
            raise KeyError(f"Unknown setting key: {key}")
        self._settings[key] = value

    def get_raw_settings(self) -> dict[str, str]:
        return dict(self._settings)

    # -- contacts --
    def upsert_contact(self, contact: ContactRecord) -> None:
        existing = self._contacts.get(contact.chat_guid)
        ai_enabled = existing.ai_enabled if existing is not None else contact.ai_enabled
        self._contacts[contact.chat_guid] = replace(contact, ai_enabled=ai_enabled)

    def list_contacts(self) -> list[ContactRecord]:
        return sorted(self._contacts.values(), key=lambda c: (c.is_group, c.display_name.lower()))

    def get_contact_by_chat_id(self, chat_id: int) -> ContactRecord | None:
        for c in self._contacts.values():
            if c.chat_id == chat_id:
                return c
        return None

    def get_contact(self, chat_guid: str) -> ContactRecord | None:
        return self._contacts.get(chat_guid)

    def set_contact_ai(self, chat_guid: str, enabled: bool) -> None:
        contact = self._contacts.get(chat_guid)
        if contact is None:
            raise KeyError(f"Unknown contact: {chat_guid}")
        if contact.is_group:
            raise ValueError("GROUP_FORBIDDEN")
        self._contacts[chat_guid] = replace(contact, ai_enabled=enabled)

    def set_contact_last_seen(self, chat_guid: str, message_guid: str) -> None:
        contact = self._contacts.get(chat_guid)
        if contact is not None:
            self._contacts[chat_guid] = replace(contact, last_seen_message_guid=message_guid)

    # -- processed messages --
    def is_processed(self, message_guid: str) -> bool:
        return message_guid in self._processed

    def record_processed(
        self,
        message_guid: str,
        chat_guid: str,
        status: str,
        error_code: str | None = None,
        reply_sent: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self._processed.get(message_guid)
        reply_sent_at = now if reply_sent else (existing["reply_sent_at"] if existing else None)
        self._processed[message_guid] = {
            "chat_guid": chat_guid,
            "received_at": now,
            "status": status,
            "error_code": error_code,
            "reply_sent_at": reply_sent_at,
            "seq": next(self._seq),
        }

    def replies_since(self, since_iso: str) -> int:
        return sum(
            1
            for r in self._processed.values()
            if r["status"] == "replied" and r["reply_sent_at"] and r["reply_sent_at"] >= since_iso
        )

    def last_reply_at(self, chat_guid: str) -> str | None:
        candidates = [
            r["reply_sent_at"]
            for r in self._processed.values()
            if r["chat_guid"] == chat_guid and r["status"] == "replied" and r["reply_sent_at"]
        ]
        return max(candidates) if candidates else None

    def recent_consecutive_failures(self) -> int:
        rows = sorted(self._processed.values(), key=lambda r: r["seq"], reverse=True)
        count = 0
        for i, row in enumerate(rows):
            status = row["status"]
            if status == "failed":
                count += 1
            elif status.startswith("skipped:"):
                continue
            else:
                return 0 if i == 0 else count
        return count


class FakeImsgClient:
    """In-test stand-in matching ImsgClient's public surface."""

    def __init__(self, chats=None) -> None:
        self.started = False
        self.stopped = False
        self._chats = chats or []
        self.history: list[Message] = []
        self.sent_messages: list[tuple[int, str]] = []
        self.health_ok = True
        self.send_should_fail = False
        self.get_history_should_fail = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def list_contacts(self, limit: int = 200):
        return self._chats

    async def get_history(self, chat_id: int, limit: int = 50) -> list[Message]:
        if self.get_history_should_fail:
            raise ImsgUnavailableError("history unavailable")
        return self.history

    async def send_message(self, chat_id: int, text: str) -> str:
        if self.send_should_fail:
            raise ImsgUnavailableError("send failed")
        self.sent_messages.append((chat_id, text))
        return "sent-guid"

    async def health_check(self) -> bool:
        return self.health_ok

    async def watch_messages(self, since_rowid: int = 0) -> AsyncIterator[Message]:
        while True:
            msg = await self._queue.get()
            if msg is None:
                return
            yield msg

    async def push(self, msg: Message) -> None:
        await self._queue.put(msg)


class FakeResponder:
    def __init__(self, reply_text: str = "sounds good!", should_raise: Exception | None = None) -> None:
        self.reply_text = reply_text
        self.should_raise = should_raise
        self.calls: list[tuple[str, str]] = []

    async def generate_reply(
        self, contact, recent_messages, incoming_message, model_id, max_reply_chars,
        style_profile: str = "", **kwargs
    ) -> str:
        self.calls.append((contact.chat_guid, model_id))
        self.style_profiles = getattr(self, "style_profiles", [])
        self.style_profiles.append(style_profile)
        if self.should_raise is not None:
            raise self.should_raise
        return self.reply_text


def make_daemon(**kwargs) -> Daemon:
    kwargs.setdefault("imsg_client", FakeImsgClient())
    kwargs.setdefault("database", FakeDatabase())
    kwargs.setdefault("responder", FakeResponder())
    return Daemon(**kwargs)


# -- pipeline: silent ignores (steps 1-3) ----------------------------------------


@pytest.mark.asyncio
async def test_from_me_ignored_no_record_but_watermark_advances():
    db = FakeDatabase()
    daemon = make_daemon(database=db)
    msg = make_message(rowid=5, guid="g1", chat_id=1, is_from_me=True)
    await daemon.process_message(msg)
    assert db.is_processed("g1") is False
    assert db.get_settings().last_seen_rowid == 5


@pytest.mark.asyncio
async def test_reaction_ignored_no_record():
    db = FakeDatabase()
    daemon = make_daemon(database=db)
    msg = make_message(rowid=6, guid="g2", chat_id=1, is_reaction=True, text="\U0001F44D")
    await daemon.process_message(msg)
    assert db.is_processed("g2") is False
    assert db.get_settings().last_seen_rowid == 6


@pytest.mark.asyncio
async def test_empty_text_ignored_no_record():
    db = FakeDatabase()
    daemon = make_daemon(database=db)
    msg = make_message(rowid=7, guid="g3", chat_id=1, text="   ")
    await daemon.process_message(msg)
    assert db.is_processed("g3") is False
    assert db.get_settings().last_seen_rowid == 7


@pytest.mark.asyncio
async def test_duplicate_guid_second_event_ignored_no_second_send():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    daemon = make_daemon(database=db, imsg_client=imsg, responder=FakeResponder())

    msg = make_message(rowid=1, guid="dup-guid", chat_id=1, text="hello")
    await daemon.process_message(msg)
    assert len(imsg.sent_messages) == 1
    assert db._processed["dup-guid"]["status"] == "replied"

    # Same guid arrives again (e.g. re-delivered notification): must be a no-op.
    await daemon.process_message(msg)
    assert len(imsg.sent_messages) == 1


# -- pipeline: policy skips (steps 5-12, delegated) ------------------------------


@pytest.mark.asyncio
async def test_group_chat_skips_and_records():
    db = FakeDatabase()
    db.upsert_contact(make_contact(chat_guid="g-chat", chat_id=1, is_group=True, ai_enabled=False))
    daemon = make_daemon(database=db)
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)
    assert db._processed["g1"]["status"] == "skipped:group"
    assert db.get_settings().last_seen_rowid == 1


@pytest.mark.asyncio
async def test_paused_skips_and_records():
    db = FakeDatabase()
    db.set_setting("paused", "true")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    daemon = make_daemon(database=db)
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)
    assert db._processed["g1"]["status"] == "skipped:paused"


@pytest.mark.asyncio
async def test_contact_off_skips_and_records():
    db = FakeDatabase()
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=False))
    daemon = make_daemon(database=db)
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)
    assert db._processed["g1"]["status"] == "skipped:contact_off"


@pytest.mark.asyncio
async def test_unknown_contact_after_sync_still_missing_skips():
    db = FakeDatabase()
    imsg = FakeImsgClient(chats=[])  # sync finds nothing
    daemon = make_daemon(database=db, imsg_client=imsg)
    msg = make_message(rowid=1, guid="g1", chat_id=999, text="hi")
    await daemon.process_message(msg)
    record = db._processed["g1"]
    assert record["status"] == "skipped:unknown_contact"
    assert record["chat_guid"] == "unknown:999"


@pytest.mark.asyncio
async def test_auto_pause_triggers_and_sets_paused():
    db = FakeDatabase()
    db.set_setting("failure_pause_threshold", "2")
    db.set_setting("selected_model_id", "claude-test")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    db.record_processed("prev1", "c1", "failed", error_code="SEND_FAILED")
    db.record_processed("prev2", "c1", "failed", error_code="SEND_FAILED")
    daemon = make_daemon(database=db)

    msg = make_message(rowid=3, guid="g3", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert db.get_settings().paused is True
    assert db._processed["g3"]["status"] == "skipped:auto_paused"


# -- pipeline: step 13 delay re-check --------------------------------------------


@pytest.mark.asyncio
async def test_toggle_flip_during_delay_aborts_as_skipped(monkeypatch):
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    daemon = make_daemon(database=db, imsg_client=imsg)

    async def flipping_delay(seconds: float) -> None:
        # Simulate the toggle being flipped off by the TUI while we wait.
        db.set_setting("global_ai_enabled", "false")

    monkeypatch.setattr(daemon_module, "apply_response_delay", flipping_delay)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert db._processed["g1"]["status"] == "skipped:global_off"
    assert imsg.sent_messages == []


@pytest.mark.asyncio
async def test_busy_chat_skips_silently_no_record():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    daemon = make_daemon(database=db)
    assert daemon.scheduler.try_acquire("c1") is True  # simulate an in-flight reply

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert "g1" not in db._processed
    assert db.get_settings().last_seen_rowid == 1


# -- pipeline: steps 14-17 failure/success paths ---------------------------------


@pytest.mark.asyncio
async def test_successful_reply_recorded_and_sent():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    responder = FakeResponder(reply_text="hey there!")
    daemon = make_daemon(database=db, imsg_client=imsg, responder=responder)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    record = db._processed["g1"]
    assert record["status"] == "replied"
    assert record["reply_sent_at"] is not None
    assert imsg.sent_messages == [(1, "hey there!")]
    assert db.get_contact("c1").last_seen_message_guid == "g1"
    assert responder.calls == [("c1", "claude-test")]


@pytest.mark.asyncio
async def test_get_history_failure_records_imsg_unavailable():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    imsg.get_history_should_fail = True
    daemon = make_daemon(database=db, imsg_client=imsg)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    record = db._processed["g1"]
    assert record["status"] == "failed"
    assert record["error_code"] == "IMSG_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_api_key_records_no_api_key():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    daemon = Daemon(
        imsg_client=FakeImsgClient(),
        database=db,
        responder=None,
        api_key_getter=lambda: None,
    )

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    record = db._processed["g1"]
    assert record["status"] == "failed"
    assert record["error_code"] == "NO_API_KEY"


@pytest.mark.asyncio
async def test_no_model_selected_records_no_model():
    db = FakeDatabase()  # selected_model_id defaults to ""
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    daemon = make_daemon(database=db)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    record = db._processed["g1"]
    assert record["error_code"] == "NO_MODEL"


@pytest.mark.asyncio
async def test_anthropic_timeout_records_anthropic_timeout():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    responder = FakeResponder(should_raise=AnthropicUnavailableError("timeout: took too long"))
    daemon = make_daemon(database=db, responder=responder)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert db._processed["g1"]["error_code"] == "ANTHROPIC_TIMEOUT"


@pytest.mark.asyncio
async def test_anthropic_generic_error_records_anthropic_error():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    responder = FakeResponder(should_raise=AnthropicUnavailableError("connection refused"))
    daemon = make_daemon(database=db, responder=responder)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert db._processed["g1"]["error_code"] == "ANTHROPIC_ERROR"


@pytest.mark.asyncio
async def test_validation_error_records_validation_failed():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    responder = FakeResponder(should_raise=ReplyValidationError("empty after validation"))
    daemon = make_daemon(database=db, responder=responder)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    assert db._processed["g1"]["error_code"] == "VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_send_failure_records_send_failed():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    imsg.send_should_fail = True
    daemon = make_daemon(database=db, imsg_client=imsg)

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await daemon.process_message(msg)

    record = db._processed["g1"]
    assert record["status"] == "failed"
    assert record["error_code"] == "SEND_FAILED"
    assert imsg.sent_messages == []


# -- watch loop wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_loop_processes_pushed_messages_and_advances_watermark():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    imsg = FakeImsgClient()
    daemon = make_daemon(database=db, imsg_client=imsg)

    task = asyncio.create_task(daemon._watch_loop())
    try:
        await imsg.push(make_message(rowid=9, guid="g9", chat_id=1, text="hi"))
        for _ in range(100):
            if db.is_processed("g9"):
                break
            await asyncio.sleep(0.01)
        assert db.is_processed("g9")
        assert db.get_settings().last_seen_rowid == 9
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- socket protocol (ARCHITECTURE §5) --------------------------------------------


@pytest.mark.asyncio
async def test_socket_ping_round_trip(monkeypatch):
    # macOS AF_UNIX paths are capped at ~104 bytes; pytest's tmp_path is often
    # too deep, so use a short path directly under /tmp for the real socket.
    socket_path = Path(f"/tmp/tfm-{uuid.uuid4().hex[:10]}.sock")
    monkeypatch.setattr(config, "SOCKET_PATH", socket_path)
    daemon = make_daemon()
    await daemon._start_socket_server()
    try:
        assert config.SOCKET_PATH.exists()
        mode = config.SOCKET_PATH.stat().st_mode & 0o777
        assert mode == 0o600

        reader, writer = await asyncio.open_unix_connection(str(config.SOCKET_PATH))
        writer.write(json.dumps({"id": 1, "method": "ping", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        daemon._server.close()
        with contextlib.suppress(Exception):
            await daemon._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()

    resp = json.loads(line)
    assert resp == {"id": 1, "ok": True, "result": {}}


@pytest.mark.asyncio
async def test_socket_malformed_json_bad_params_null_id():
    daemon = make_daemon()
    resp = await daemon._handle_request_line(b"not-json{{{")
    assert resp["id"] is None
    assert resp["ok"] is False
    assert resp["error"]["code"] == "BAD_PARAMS"


@pytest.mark.asyncio
async def test_socket_unknown_method():
    daemon = make_daemon()
    resp = await daemon._handle_request_line(json.dumps({"id": 2, "method": "nope", "params": {}}).encode())
    assert resp["ok"] is False
    assert resp["error"]["code"] == "UNKNOWN_METHOD"


@pytest.mark.asyncio
async def test_socket_status():
    db = FakeDatabase()
    daemon = make_daemon(database=db)
    resp = await daemon._handle_request_line(json.dumps({"id": 3, "method": "status", "params": {}}).encode())
    assert resp["ok"] is True
    result = resp["result"]
    assert result["running"] is True
    assert result["paused"] is False
    assert result["global_ai_enabled"] is True
    assert "imsg_ok" in result
    assert "replies_last_hour" in result
    assert result["last_error"] is None


@pytest.mark.asyncio
async def test_socket_contacts_list():
    db = FakeDatabase()
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, display_name="Alice"))
    daemon = make_daemon(database=db)
    resp = await daemon._handle_request_line(
        json.dumps({"id": 4, "method": "contacts.list", "params": {}}).encode()
    )
    assert resp["ok"] is True
    assert resp["result"]["contacts"][0]["chat_guid"] == "c1"
    assert resp["result"]["contacts"][0]["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_socket_contacts_set_ai_group_forbidden():
    db = FakeDatabase()
    db.upsert_contact(make_contact(chat_guid="group-1", chat_id=1, is_group=True, ai_enabled=False))
    daemon = make_daemon(database=db)
    resp = await daemon._handle_request_line(
        json.dumps(
            {"id": 5, "method": "contacts.set_ai", "params": {"chat_guid": "group-1", "enabled": True}}
        ).encode()
    )
    assert resp["ok"] is False
    assert resp["error"]["code"] == "GROUP_FORBIDDEN"


@pytest.mark.asyncio
async def test_socket_contacts_set_ai_success():
    db = FakeDatabase()
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=False))
    daemon = make_daemon(database=db)
    resp = await daemon._handle_request_line(
        json.dumps({"id": 6, "method": "contacts.set_ai", "params": {"chat_guid": "c1", "enabled": True}}).encode()
    )
    assert resp["ok"] is True
    assert db.get_contact("c1").ai_enabled is True


@pytest.mark.asyncio
async def test_socket_contacts_refresh():
    from textforme.messaging.models import Chat

    db = FakeDatabase()
    imsg = FakeImsgClient(chats=[Chat(chat_id=1, guid="c1", display_name="Alice", is_group=False)])
    daemon = make_daemon(database=db, imsg_client=imsg)
    resp = await daemon._handle_request_line(
        json.dumps({"id": 7, "method": "contacts.refresh", "params": {}}).encode()
    )
    assert resp["ok"] is True
    assert resp["result"]["count"] == 1
    assert db.get_contact("c1") is not None


@pytest.mark.asyncio
async def test_socket_settings_get_and_set():
    db = FakeDatabase()
    daemon = make_daemon(database=db)

    resp = await daemon._handle_request_line(json.dumps({"id": 8, "method": "settings.get", "params": {}}).encode())
    assert resp["ok"] is True
    assert "maximum_reply_length" in resp["result"]["settings"]

    resp = await daemon._handle_request_line(
        json.dumps({"id": 9, "method": "settings.set", "params": {"key": "maximum_reply_length", "value": "150"}}).encode()
    )
    assert resp["ok"] is True
    assert db.get_settings().maximum_reply_length == 150


@pytest.mark.asyncio
async def test_socket_settings_set_unknown_key():
    daemon = make_daemon()
    resp = await daemon._handle_request_line(
        json.dumps({"id": 10, "method": "settings.set", "params": {"key": "not_a_real_key", "value": "x"}}).encode()
    )
    assert resp["ok"] is False
    assert resp["error"]["code"] == "UNKNOWN_KEY"


@pytest.mark.asyncio
async def test_socket_pause_resume():
    db = FakeDatabase()
    daemon = make_daemon(database=db)

    resp = await daemon._handle_request_line(json.dumps({"id": 11, "method": "service.pause", "params": {}}).encode())
    assert resp["ok"] is True
    assert db.get_settings().paused is True

    resp = await daemon._handle_request_line(json.dumps({"id": 12, "method": "service.resume", "params": {}}).encode())
    assert resp["ok"] is True
    assert db.get_settings().paused is False


# -- startup resilience -----------------------------------------------------------


async def test_daemon_starts_and_serves_status_when_initial_sync_fails(monkeypatch):
    """If the initial contact sync fails (e.g. Full Disk Access not granted in
    the launchd context), the daemon must NOT crash-loop: it starts, serves the
    socket so the TUI can show status, and surfaces the error in last_error."""
    sock = Path(f"/tmp/tfm-{uuid.uuid4().hex[:8]}.sock")
    monkeypatch.setattr(config, "SOCKET_PATH", sock)

    class SyncFailingImsg(FakeImsgClient):
        async def list_contacts(self, limit: int = 200):
            raise ImsgUnavailableError("chat.db not readable (no Full Disk Access)")

    daemon = make_daemon(imsg_client=SyncFailingImsg())
    task = asyncio.create_task(daemon.run())
    try:
        for _ in range(200):
            if sock.exists():
                break
            await asyncio.sleep(0.01)
        assert sock.exists(), "socket server never came up after failed initial sync"

        response = await daemon._handle_request_line(b'{"id": 1, "method": "status", "params": {}}')
        assert response["ok"] is True
        assert response["result"]["running"] is True
        assert "sync failed" in (response["result"]["last_error"] or "")
    finally:
        daemon.request_shutdown()
        await asyncio.wait_for(task, timeout=5)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()


async def test_daemon_retries_failed_initial_sync_until_recovery(monkeypatch):
    """After a failed startup sync, the daemon keeps retrying in the background
    and clears last_error once the sync succeeds (e.g. FDA granted later)."""
    sock = Path(f"/tmp/tfm-{uuid.uuid4().hex[:8]}.sock")
    monkeypatch.setattr(config, "SOCKET_PATH", sock)
    monkeypatch.setattr(daemon_module, "_SYNC_RETRY_INTERVAL", 0.01)

    class EventuallyHealthyImsg(FakeImsgClient):
        def __init__(self) -> None:
            super().__init__(chats=[])
            self.sync_attempts = 0

        async def list_contacts(self, limit: int = 200):
            self.sync_attempts += 1
            if self.sync_attempts < 3:
                raise ImsgUnavailableError("no Full Disk Access yet")
            return []

    imsg = EventuallyHealthyImsg()
    daemon = make_daemon(imsg_client=imsg)
    task = asyncio.create_task(daemon.run())
    try:
        for _ in range(500):
            if imsg.sync_attempts >= 3 and daemon._last_error is None:
                break
            await asyncio.sleep(0.01)
        assert imsg.sync_attempts >= 3
        assert daemon._last_error is None
    finally:
        daemon.request_shutdown()
        await asyncio.wait_for(task, timeout=5)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()


# -- models.list socket method ----------------------------------------------------


async def test_models_list_returns_models_and_caches():
    from textforme.anthropic.models import ModelInfo

    calls = []

    class FakeAnthropicClient:
        async def list_models(self):
            calls.append(1)
            return [ModelInfo("claude-a", "Claude A"), ModelInfo("claude-b", "Claude B")]

    daemon = make_daemon(
        api_key_getter=lambda: "sk-test",
        anthropic_client_factory=lambda key: FakeAnthropicClient(),
    )
    request = b'{"id": 1, "method": "models.list", "params": {}}'
    response = await daemon._handle_request_line(request)
    assert response["ok"] is True
    assert response["result"]["models"] == [
        {"model_id": "claude-a", "display_name": "Claude A"},
        {"model_id": "claude-b", "display_name": "Claude B"},
    ]
    # Second call is served from the cache — no new API fetch.
    await daemon._handle_request_line(request)
    assert len(calls) == 1


async def test_models_list_without_key_returns_no_api_key():
    daemon = make_daemon(api_key_getter=lambda: None)
    response = await daemon._handle_request_line(b'{"id": 1, "method": "models.list", "params": {}}')
    assert response["ok"] is False
    assert response["error"]["code"] == "NO_API_KEY"


async def test_models_list_api_failure_returns_anthropic_unavailable():
    class FailingClient:
        async def list_models(self):
            raise AnthropicUnavailableError("api down")

    daemon = make_daemon(
        api_key_getter=lambda: "sk-test",
        anthropic_client_factory=lambda key: FailingClient(),
    )
    response = await daemon._handle_request_line(b'{"id": 1, "method": "models.list", "params": {}}')
    assert response["ok"] is False
    assert response["error"]["code"] == "ANTHROPIC_UNAVAILABLE"


async def test_style_profile_from_settings_reaches_responder():
    db = FakeDatabase()
    db.set_setting("selected_model_id", "claude-test")
    db.set_setting("response_delay_seconds", "0")
    db.set_setting("style_profile", "short, lowercase, friendly")
    imsg = FakeImsgClient(chats=[])
    responder = FakeResponder()
    daemon = make_daemon(database=db, imsg_client=imsg, responder=responder)
    db.upsert_contact(make_contact(chat_guid="c1", chat_id=1, ai_enabled=True))
    await daemon.process_message(make_message(rowid=1, guid="g1", chat_id=1, text="hi"))
    assert responder.style_profiles == ["short, lowercase, friendly"]
