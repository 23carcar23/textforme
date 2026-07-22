"""Shared fixtures/fakes for integration + security tests (Agent 7 owns this file).

Provides:
- FakeImsgClient: in-memory stand-in for messaging.client.ImsgClient. Seeded
  chats, per-chat history, an asyncio queue for pushing watch() events, and a
  record of every send_message call + every since_rowid passed to
  watch_messages (for restart-recovery assertions).
- FakeAnthropicClient: stand-in for anthropic.client.AnthropicClient's public
  surface (just `complete`). Records every call's kwargs (model_id, system,
  messages, max_tokens) and supports canned replies / programmable failures
  / artificial delay. Wrapped in the REAL AnthropicResponder + REAL
  anthropic.prompts.build_request so integration/security tests exercise the
  real prompt-construction and reply-validation code paths.
- DaemonHarness: a real database.Database (backed by a tmp_path sqlite file)
  plus a real daemon.Daemon, served over a real (short, /tmp-rooted) Unix
  socket, wired to the fakes above. Runs the daemon as a background asyncio
  task and tears it down cleanly.
- daemon_harness_factory: pytest fixture yielding an async factory for the
  above, auto-stopping every harness it created at test teardown.
- read_processed_row / wait_for_processed: helpers to inspect
  processed_messages rows without reaching into daemon internals.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from textforme import config
from textforme.daemon import Daemon
from textforme.database import ContactRecord, Database
from textforme.messaging.events import AnthropicUnavailableError, ImsgUnavailableError
from textforme.messaging.models import Chat, Message
from textforme.service.responder import AnthropicResponder

# Re-exported for convenience so test modules can `from tests.conftest import make_contact, make_message`
from tests.fixtures.factories import make_contact, make_message  # noqa: F401

__all__ = [
    "FakeImsgClient",
    "FakeAnthropicClient",
    "DaemonHarness",
    "short_socket_path",
    "read_processed_row",
    "wait_for_processed",
    "make_contact",
    "make_message",
]


# -- fakes --------------------------------------------------------------------


class FakeImsgClient:
    """In-memory stand-in matching messaging.client.ImsgClient's public surface."""

    def __init__(self, chats: list[Chat] | None = None) -> None:
        self.started = False
        self.stopped = False
        self.chats: list[Chat] = list(chats or [])
        self.history_by_chat: dict[int, list[Message]] = {}
        self.sent_messages: list[dict[str, Any]] = []
        self.health_ok = True
        self.send_should_fail = False
        self.get_history_should_fail = False
        # Every since_rowid a watch_messages() call was started with -- used to
        # assert restart recovery resubscribes from the persisted watermark.
        self.watch_subscribe_since_rowids: list[int] = []
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def list_contacts(self, limit: int = 200) -> list[Chat]:
        return list(self.chats)

    async def get_history(self, chat_id: int, limit: int = 50) -> list[Message]:
        if self.get_history_should_fail:
            raise ImsgUnavailableError("history unavailable")
        return list(self.history_by_chat.get(chat_id, []))[-limit:]

    async def send_message(self, chat_id: int, text: str) -> str:
        if self.send_should_fail:
            raise ImsgUnavailableError("send failed")
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return f"sent-guid-{len(self.sent_messages)}"

    async def health_check(self) -> bool:
        return self.health_ok

    async def watch_messages(self, since_rowid: int = 0) -> AsyncIterator[Message]:
        self.watch_subscribe_since_rowids.append(since_rowid)
        while True:
            msg = await self._queue.get()
            if msg is None:
                return
            yield msg

    async def push(self, msg: Message) -> None:
        """Simulate an incoming watch notification."""
        await self._queue.put(msg)


class FakeAnthropicClient:
    """Stand-in for anthropic.client.AnthropicClient's `complete` surface only.

    Wrapped in a real AnthropicResponder so the real system prompt / message
    assembly / reply validation always run -- only the network call is faked.
    """

    def __init__(self, reply: str = "sounds good, see you then!") -> None:
        self.default_reply = reply
        self.queued_replies: list[str] = []
        self.calls: list[dict[str, Any]] = []
        # If set, every call raises this exception (unless fail_times limits it).
        self.fail_with: Exception | None = None
        # If >0, only the first N calls fail with fail_with; afterwards succeeds.
        self.fail_times: int = 0
        self.delay_seconds: float = 0.0
        self._fail_count = 0

    async def complete(
        self,
        model_id: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> str:
        self.calls.append(
            {
                "model_id": model_id,
                "system": system,
                "messages": messages,
                "max_tokens": max_tokens,
            }
        )
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_with is not None:
            self._fail_count += 1
            if self.fail_times <= 0 or self._fail_count <= self.fail_times:
                raise self.fail_with
        if self.queued_replies:
            return self.queued_replies.pop(0)
        return self.default_reply

    def fail_always(self, exc: Exception | None = None) -> None:
        self.fail_with = exc or AnthropicUnavailableError("simulated Anthropic failure")
        self.fail_times = 0

    def fail_next(self, count: int, exc: Exception | None = None) -> None:
        self.fail_with = exc or AnthropicUnavailableError("simulated Anthropic failure")
        self.fail_times = count


def short_socket_path() -> Path:
    """A /tmp-rooted socket path short enough for macOS's 104-byte AF_UNIX cap."""
    return Path(f"/tmp/tfm-{uuid.uuid4().hex[:10]}.sock")


# -- harness --------------------------------------------------------------------


class DaemonHarness:
    """A real Database + real Daemon, served over a real Unix socket, wired to fakes.

    Use as an async context manager, or via the daemon_harness_factory fixture
    which also guarantees teardown.
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        database: Database | None = None,
        imsg: FakeImsgClient | None = None,
        anthropic: FakeAnthropicClient | None = None,
        contacts: list[ContactRecord] | None = None,
        settings: dict[str, str] | None = None,
        api_key: str | None = "sk-ant-test-key",
        socket_path: Path | None = None,
    ) -> None:
        if database is not None:
            self.database = database
        else:
            self.database = Database(db_path or short_socket_path().with_suffix(".db"))

        self.imsg = imsg if imsg is not None else FakeImsgClient()
        self.anthropic = anthropic if anthropic is not None else FakeAnthropicClient()
        self.responder = AnthropicResponder(self.anthropic)
        self.socket_path = socket_path or short_socket_path()

        for key, value in (settings or {}).items():
            self.database.set_setting(key, value)

        for contact in contacts or []:
            self.database.upsert_contact(contact)

        self.daemon = Daemon(
            imsg_client=self.imsg,
            database=self.database,
            responder=self.responder,
            api_key_getter=(lambda: api_key),
        )
        self._mp = pytest.MonkeyPatch()
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> "DaemonHarness":
        self._mp.setattr(config, "SOCKET_PATH", self.socket_path)
        self._task = asyncio.create_task(self.daemon.run())
        for _ in range(500):
            if self.socket_path.exists():
                return self
            await asyncio.sleep(0.01)
        raise RuntimeError("daemon socket did not start listening in time")

    async def stop(self) -> None:
        self.daemon.request_shutdown()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, TimeoutError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._mp.undo()
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

    async def __aenter__(self) -> "DaemonHarness":
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    def client(self, timeout: float = 5.0):
        """A tui.app.DaemonClient pointed at this harness's real socket."""
        from textforme.tui.app import DaemonClient

        return DaemonClient(socket_path=self.socket_path, timeout=timeout)


@pytest_asyncio.fixture
async def daemon_harness_factory(tmp_path: Path):
    created: list[DaemonHarness] = []
    counter = itertools.count(1)

    async def _make(**kwargs: Any) -> DaemonHarness:
        if "database" not in kwargs:
            kwargs.setdefault("db_path", tmp_path / f"tfm-{next(counter)}.db")
        harness = DaemonHarness(**kwargs)
        await harness.start()
        created.append(harness)
        return harness

    yield _make

    for harness in reversed(created):
        with contextlib.suppress(Exception):
            await harness.stop()


@pytest.fixture
def captured_daemon_log(tmp_path: Path):
    """Attach a FileHandler to the daemon's logger for the duration of a test.

    Yields the log file path. Used by security tests to assert secrets never
    reach log output. Handler is removed at teardown regardless of outcome.
    """
    import logging

    log_path = tmp_path / "daemon.log"
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("textformed")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield log_path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()


# -- inspection helpers -----------------------------------------------------------


def read_processed_row(database: Database, message_guid: str) -> dict[str, Any] | None:
    """Read one processed_messages row directly (Database exposes no getter)."""
    cursor = database._conn.cursor()  # noqa: SLF001 - test-only introspection
    cursor.execute(
        "SELECT status, error_code, reply_sent_at, chat_guid, received_at "
        "FROM processed_messages WHERE message_guid = ?",
        (message_guid,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "status": row[0],
        "error_code": row[1],
        "reply_sent_at": row[2],
        "chat_guid": row[3],
        "received_at": row[4],
    }


async def wait_for_processed(
    database: Database, message_guid: str, timeout: float = 3.0
) -> dict[str, Any]:
    """Poll until a processed_messages row exists for message_guid."""
    deadline = time.monotonic() + timeout
    last_row: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_row = read_processed_row(database, message_guid)
        if last_row is not None:
            return last_row
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"message_guid={message_guid!r} was not recorded within {timeout}s (last_row={last_row})"
    )


async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0, interval: float = 0.01) -> None:
    """Generic poll-until-true helper for integration tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")
