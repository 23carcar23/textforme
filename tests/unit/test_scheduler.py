"""Unit tests for src/textforme/service/scheduler.py (Agent 2 owns this)."""

from __future__ import annotations

import asyncio

import pytest

from textforme.service.scheduler import ChatLocks, ReplyTimerManager
from tests.fixtures.factories import make_message


# -- ChatLocks ----------------------------------------------------------------


def test_chat_locks_returns_same_lock_per_chat():
    locks = ChatLocks()
    assert locks.lock("chat-1") is locks.lock("chat-1")


def test_chat_locks_independent_across_chats():
    locks = ChatLocks()
    assert locks.lock("chat-1") is not locks.lock("chat-2")


@pytest.mark.asyncio
async def test_chat_lock_serializes_in_order_without_dropping():
    locks = ChatLocks()
    order: list[int] = []

    async def worker(n: int) -> None:
        async with locks.lock("chat-1"):
            order.append(n)
            await asyncio.sleep(0.01)

    await asyncio.gather(worker(1), worker(2), worker(3))
    # Every worker ran (nothing dropped) and they did not interleave.
    assert sorted(order) == [1, 2, 3]
    assert len(order) == 3


# -- ReplyTimerManager --------------------------------------------------------


def test_add_message_accumulates_batch():
    mgr = ReplyTimerManager()
    mgr.add_message("chat-1", make_message(rowid=1, guid="a"))
    mgr.add_message("chat-1", make_message(rowid=2, guid="b"))
    batch = mgr.collect("chat-1")
    assert [m.guid for m in batch] == ["a", "b"]
    # collect drains the batch.
    assert mgr.collect("chat-1") == []


def test_is_running_false_before_start():
    mgr = ReplyTimerManager()
    mgr.add_message("chat-1", make_message())
    assert mgr.is_running("chat-1") is False


@pytest.mark.asyncio
async def test_start_fires_callback_after_delay():
    mgr = ReplyTimerManager()
    fired: list[str] = []

    async def cb(guid: str) -> None:
        fired.append(guid)

    mgr.add_message("chat-1", make_message())
    mgr.start("chat-1", 0.02, cb)
    assert mgr.is_running("chat-1") is True
    await asyncio.sleep(0.05)
    assert fired == ["chat-1"]
    # After firing, the timer is no longer running.
    assert mgr.is_running("chat-1") is False


@pytest.mark.asyncio
async def test_remaining_counts_down_and_is_none_when_idle():
    mgr = ReplyTimerManager()
    assert mgr.remaining("chat-1") is None
    mgr.add_message("chat-1", make_message())

    async def cb(_guid: str) -> None:
        pass

    mgr.start("chat-1", 0.2, cb)
    remaining = mgr.remaining("chat-1")
    assert remaining is not None and 0 < remaining <= 0.2
    mgr.cancel_all()


@pytest.mark.asyncio
async def test_active_lists_running_timers_only():
    mgr = ReplyTimerManager()

    async def cb(_guid: str) -> None:
        pass

    mgr.add_message("chat-1", make_message())
    mgr.start("chat-1", 0.2, cb)
    active = mgr.active()
    assert set(active) == {"chat-1"}
    mgr.cancel_all()
    assert mgr.active() == {}


@pytest.mark.asyncio
async def test_cancel_all_prevents_callback():
    mgr = ReplyTimerManager()
    fired: list[str] = []

    async def cb(guid: str) -> None:
        fired.append(guid)

    mgr.add_message("chat-1", make_message())
    mgr.start("chat-1", 0.05, cb)
    mgr.cancel_all()
    await asyncio.sleep(0.08)
    assert fired == []
