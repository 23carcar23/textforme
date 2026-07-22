"""Reply pacing helpers. Owner: Agent 2.

Two pieces of in-memory pacing state the daemon owns:

- ChatLocks: one asyncio.Lock per chat so replies to a single chat are sent
  one at a time, in arrival order (queue semantics — nothing is dropped).
- ReplyTimerManager: the "realistic texting" batching timer. When a contact
  has the reply timer enabled, the first message of a burst starts a random
  countdown; messages that arrive while it runs are accumulated instead of
  starting a new timer, and one batched reply fires when it expires.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..messaging.models import Message


class ChatLocks:
    """Lazily-created per-chat asyncio.Lock registry.

    Awaiting ``lock(chat_guid)`` serializes replies to one chat without
    dropping any — unlike a busy-set, a second waiter queues behind the first.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, chat_guid: str) -> asyncio.Lock:
        existing = self._locks.get(chat_guid)
        if existing is None:
            existing = asyncio.Lock()
            self._locks[chat_guid] = existing
        return existing


@dataclass
class _Batch:
    """Accumulated messages plus the running countdown task for one chat."""

    messages: list[Message] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    deadline: float = 0.0  # time.monotonic() value the timer fires at


class ReplyTimerManager:
    """Per-chat batching timers for the realistic-texting feature.

    The daemon calls ``add_message`` for every batched inbound text, then
    ``start`` only when no timer is already running for that chat. When the
    countdown expires the manager invokes the supplied async callback with the
    chat_guid; the callback calls ``collect`` to drain the accumulated batch.
    """

    def __init__(self) -> None:
        self._batches: dict[str, _Batch] = {}

    def add_message(self, chat_guid: str, msg: Message) -> None:
        """Append a message to the chat's pending batch (creating it if new)."""
        batch = self._batches.get(chat_guid)
        if batch is None:
            batch = _Batch()
            self._batches[chat_guid] = batch
        batch.messages.append(msg)

    def is_running(self, chat_guid: str) -> bool:
        """True if a countdown is currently ticking for this chat."""
        batch = self._batches.get(chat_guid)
        return batch is not None and batch.task is not None and not batch.task.done()

    def start(
        self,
        chat_guid: str,
        delay: float,
        callback: Callable[[str], Awaitable[None]],
    ) -> asyncio.Task[None]:
        """Start the countdown; ``callback(chat_guid)`` runs when it expires.

        Assumes ``add_message`` has already created the batch for this chat.
        """
        batch = self._batches.get(chat_guid)
        if batch is None:
            batch = _Batch()
            self._batches[chat_guid] = batch
        batch.deadline = time.monotonic() + max(0.0, delay)

        async def _run() -> None:
            try:
                await asyncio.sleep(max(0.0, delay))
            except asyncio.CancelledError:
                return
            await callback(chat_guid)

        batch.task = asyncio.create_task(_run())
        return batch.task

    def remaining(self, chat_guid: str) -> float | None:
        """Seconds left on the countdown, or None if no timer is running."""
        batch = self._batches.get(chat_guid)
        if batch is None or batch.task is None or batch.task.done():
            return None
        return max(0.0, batch.deadline - time.monotonic())

    def active(self) -> dict[str, float]:
        """Map of chat_guid -> seconds remaining for every running timer."""
        return {
            guid: max(0.0, batch.deadline - time.monotonic())
            for guid, batch in self._batches.items()
            if batch.task is not None and not batch.task.done()
        }

    def collect(self, chat_guid: str) -> list[Message]:
        """Drain and clear a chat's batch (messages oldest→newest).

        Clearing the batch means the next inbound message starts a fresh
        countdown, which is exactly the desired post-reply behavior.
        """
        batch = self._batches.pop(chat_guid, None)
        return batch.messages if batch is not None else []

    def cancel_all(self) -> None:
        """Cancel every running countdown (used on shutdown)."""
        for batch in self._batches.values():
            if batch.task is not None and not batch.task.done():
                batch.task.cancel()
        self._batches.clear()
