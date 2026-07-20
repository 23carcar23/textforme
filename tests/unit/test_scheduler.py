"""Unit tests for src/textforme/service/scheduler.py (Agent 2 owns this)."""

from __future__ import annotations

import asyncio
import time

import pytest

from textforme.service.scheduler import ReplyScheduler, apply_response_delay


# -- ReplyScheduler -----------------------------------------------------------


def test_try_acquire_succeeds_when_free():
    scheduler = ReplyScheduler()
    assert scheduler.try_acquire("chat-1") is True


def test_try_acquire_fails_when_already_in_flight():
    scheduler = ReplyScheduler()
    assert scheduler.try_acquire("chat-1") is True
    assert scheduler.try_acquire("chat-1") is False


def test_try_acquire_independent_across_chats():
    scheduler = ReplyScheduler()
    assert scheduler.try_acquire("chat-1") is True
    assert scheduler.try_acquire("chat-2") is True


def test_release_allows_reacquire():
    scheduler = ReplyScheduler()
    assert scheduler.try_acquire("chat-1") is True
    scheduler.release("chat-1")
    assert scheduler.try_acquire("chat-1") is True


def test_release_unknown_chat_is_noop():
    scheduler = ReplyScheduler()
    # Should not raise even though "chat-1" was never acquired.
    scheduler.release("chat-1")
    assert scheduler.try_acquire("chat-1") is True


# -- apply_response_delay ------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_response_delay_sleeps_requested_duration(monkeypatch):
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await apply_response_delay(5.0)
    assert captured == [5.0]


@pytest.mark.asyncio
async def test_apply_response_delay_clamps_negative_to_zero(monkeypatch):
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await apply_response_delay(-10.0)
    assert captured == [0.0]


@pytest.mark.asyncio
async def test_apply_response_delay_clamps_above_120(monkeypatch):
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await apply_response_delay(500.0)
    assert captured == [120.0]


@pytest.mark.asyncio
async def test_apply_response_delay_actually_waits():
    start = time.monotonic()
    await apply_response_delay(0.05)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04
