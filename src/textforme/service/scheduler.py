"""Reply pacing helpers. Owner: Agent 2.

Pure/near-pure helpers the daemon uses for step 13 of the pipeline and for
in-memory pacing state that complements the DB-backed cooldown/rate queries.
"""

from __future__ import annotations

import asyncio


class ReplyScheduler:
    """Tracks in-flight replies so one chat never has two concurrent generations."""

    def __init__(self) -> None:
        self._in_flight: set[str] = set()

    def try_acquire(self, chat_guid: str) -> bool:
        """True and marks busy if no reply is in flight for this chat."""
        if chat_guid in self._in_flight:
            return False
        self._in_flight.add(chat_guid)
        return True

    def release(self, chat_guid: str) -> None:
        self._in_flight.discard(chat_guid)


async def apply_response_delay(seconds: float) -> None:
    """Sleep for the configured delay (clamped to 0..120)."""
    clamped = max(0.0, min(120.0, seconds))
    await asyncio.sleep(clamped)
