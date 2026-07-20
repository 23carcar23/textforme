"""Async adapter over one long-running `imsg rpc` subprocess. Owner: Agent 2.

JSON-RPC 2.0, one JSON object per line over stdin/stdout (see ARCHITECTURE §3).
Responsibilities: request/response correlation by id, watch notifications fanned
into an asyncio.Queue, subprocess supervision with exponential-backoff restart
(resubscribing with the caller-provided since_rowid on restart), and typed
errors from messaging.events. No other module may talk to imsg directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from collections.abc import AsyncIterator
from typing import Any

from .events import ImsgError, ImsgRequestError, ImsgUnavailableError
from .models import Chat, Message

# Sentinel placed on the notification queue when the reader loop ends (the
# subprocess died or its stdout hit EOF). Wakes up any watch_messages()
# consumer that's blocked waiting on the queue so it can trigger a restart.
_DEAD = object()

_DEFAULT_TIMEOUT = 15.0
_HEALTH_TIMEOUT = 5.0
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0


class ImsgClient:
    def __init__(self, binary: str = "imsg") -> None:
        self._binary = binary
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notif_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._id_counter = itertools.count(1)
        self._write_lock = asyncio.Lock()
        self._stopping = False

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Spawn `imsg rpc`. Raises ImsgUnavailableError if the binary is missing."""
        await self._spawn()

    async def _spawn(self) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self._binary,
                "rpc",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ImsgUnavailableError(f"imsg binary not found: {self._binary}") from exc
        except OSError as exc:
            raise ImsgUnavailableError(f"failed to start imsg: {exc}") from exc

        self._process = process
        self._stopping = False
        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Terminate the subprocess and cancel readers. Idempotent."""
        self._stopping = True

        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, TimeoutError):
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.wait()

        self._fail_all_pending(ImsgUnavailableError("imsg client stopped"))

    def _fail_all_pending(self, exc: Exception) -> None:
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    # -- reader loop -----------------------------------------------------------

    async def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("method") == "message":
                    await self._notif_queue.put(obj.get("params") or {})
                elif "id" in obj and obj.get("id") is not None:
                    self._resolve_response(obj)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._fail_all_pending(ImsgUnavailableError("imsg process ended"))
            with contextlib.suppress(Exception):
                self._notif_queue.put_nowait(_DEAD)

    def _resolve_response(self, obj: dict[str, Any]) -> None:
        req_id = obj.get("id")
        fut = self._pending.pop(req_id, None)
        if fut is None or fut.done():
            return
        error = obj.get("error")
        if error:
            code = error.get("code", -1) if isinstance(error, dict) else -1
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            fut.set_exception(ImsgRequestError(code, message))
        else:
            fut.set_result(obj.get("result"))

    # -- request/response ------------------------------------------------------

    async def _call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = _DEFAULT_TIMEOUT
    ) -> Any:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise ImsgUnavailableError("imsg process not running")

        req_id = next(self._id_counter)
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        data = (json.dumps(payload) + "\n").encode("utf-8")

        async with self._write_lock:
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                self._pending.pop(req_id, None)
                raise ImsgUnavailableError(f"imsg pipe broken: {exc}") from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            self._pending.pop(req_id, None)
            raise ImsgUnavailableError(f"imsg request '{method}' timed out") from exc

    # -- public RPC surface -----------------------------------------------------

    async def list_contacts(self, limit: int = 200) -> list[Chat]:
        """chats.list."""
        result = await self._call("chats.list", {"limit": limit})
        chats = (result or {}).get("chats") or []
        return [Chat.from_json(c) for c in chats]

    async def get_history(self, chat_id: int, limit: int = 50) -> list[Message]:
        """messages.history, oldest→newest."""
        result = await self._call("messages.history", {"chat_id": chat_id, "limit": limit})
        raw_messages = (result or {}).get("messages") or []
        messages = [Message.from_json(m) for m in raw_messages]
        messages.sort(key=lambda m: m.rowid)
        return messages

    async def send_message(self, chat_id: int, text: str) -> str:
        """send; returns the sent message guid ('' if not reported)."""
        result = await self._call("send", {"chat_id": chat_id, "text": text})
        return str((result or {}).get("guid") or "")

    async def health_check(self) -> bool:
        """chats.list limit=1 with a short timeout; False instead of raising."""
        try:
            await self._call("chats.list", {"limit": 1}, timeout=_HEALTH_TIMEOUT)
            return True
        except Exception:
            return False

    async def watch_messages(self, since_rowid: int = 0) -> AsyncIterator[Message]:
        """watch.subscribe (include_reactions=True so reactions can be filtered);
        yields every notification Message forever, transparently resubscribing
        from the highest rowid seen if the subprocess dies (sleep/wake safe)."""
        highest = since_rowid
        backoff = _INITIAL_BACKOFF

        while True:
            if self._process is None or self._process.returncode is not None:
                try:
                    await self._spawn()
                except ImsgUnavailableError:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, _MAX_BACKOFF)
                    continue

            try:
                params: dict[str, Any] = {"include_reactions": True, "debounce_ms": 500}
                if highest:
                    params["since_rowid"] = highest
                await self._call("watch.subscribe", params, timeout=_DEFAULT_TIMEOUT)
            except ImsgError:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _MAX_BACKOFF)
                continue

            backoff = _INITIAL_BACKOFF

            while True:
                item = await self._notif_queue.get()
                if item is _DEAD:
                    break
                message_payload = item.get("message", item) if isinstance(item, dict) else item
                msg = Message.from_json(message_payload)
                if msg.rowid > highest:
                    highest = msg.rowid
                yield msg
