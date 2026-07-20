"""End-to-end pipeline integration tests: real Database, real policies, real
Daemon (ARCHITECTURE.md §6), fake imsg/anthropic network edges.

Owner: Agent 7 (testing). Includes a regression test for the concurrent-burst
rate-limit race found while writing these (fixed in daemon.py step 13).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.conftest import (
    make_contact,
    make_message,
    wait_for_processed,
)
from textforme.messaging.events import AnthropicUnavailableError


# -- 1. happy path ------------------------------------------------------------


async def test_happy_path_enabled_contact_gets_exactly_one_reply(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    harness.anthropic.default_reply = "hey! good to hear from you"

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi there")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "replied"
    assert row["reply_sent_at"] is not None

    assert harness.imsg.sent_messages == [{"chat_id": 1, "text": "hey! good to hear from you"}]
    assert len(harness.anthropic.calls) == 1

    # watermark advanced
    assert harness.database.get_settings().last_seen_rowid == 1


# -- 2. disabled/enabled contacts ---------------------------------------------


async def test_disabled_contact_never_replied_to(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=False)],
        settings={"selected_model_id": "claude-test"},
    )
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi there")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "skipped:contact_off"
    assert harness.imsg.sent_messages == []


# -- 5. groups ------------------------------------------------------------------


async def test_group_chat_message_skipped_and_never_sent(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="grp1", chat_id=1, is_group=True, ai_enabled=False)],
        settings={"selected_model_id": "claude-test"},
    )
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi group")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "skipped:group"
    assert harness.imsg.sent_messages == []
    assert harness.anthropic.calls == []


# -- 6. rate limit + quiet hours -------------------------------------------------


async def test_rate_limit_enforced_sequential_delivery(daemon_harness_factory):
    """Sequential delivery (each message fully processed before the next is
    pushed) is the deterministic way to exercise the rate limiter -- the next
    test covers concurrent delivery of a burst."""
    harness = await daemon_harness_factory(
        contacts=[
            make_contact(chat_guid="c1", chat_id=1, ai_enabled=True),
            make_contact(chat_guid="c2", chat_id=2, ai_enabled=True),
            make_contact(chat_guid="c3", chat_id=3, ai_enabled=True),
        ],
        settings={
            "selected_model_id": "claude-test",
            "global_rate_limit_per_hour": "2",
        },
    )

    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="hi1"))
    row1 = await wait_for_processed(harness.database, "g1")
    await harness.imsg.push(make_message(rowid=2, guid="g2", chat_id=2, text="hi2"))
    row2 = await wait_for_processed(harness.database, "g2")
    await harness.imsg.push(make_message(rowid=3, guid="g3", chat_id=3, text="hi3"))
    row3 = await wait_for_processed(harness.database, "g3")

    assert row1["status"] == "replied"
    assert row2["status"] == "replied"
    assert row3["status"] == "skipped:rate_limit"
    assert len(harness.imsg.sent_messages) == 2


async def test_rate_limit_race_under_truly_concurrent_delivery(daemon_harness_factory):
    """Regression test: the daemon's post-delay full-policy re-check under the
    rate lock (with in-flight replies counted as reserved capacity) must keep a
    concurrent burst from distinct contacts within global_rate_limit_per_hour.
    Previously all three tasks snapshotted the same stale replies_last_hour and
    all three sends went out."""
    harness = await daemon_harness_factory(
        contacts=[
            make_contact(chat_guid="c1", chat_id=1, ai_enabled=True),
            make_contact(chat_guid="c2", chat_id=2, ai_enabled=True),
            make_contact(chat_guid="c3", chat_id=3, ai_enabled=True),
        ],
        settings={
            "selected_model_id": "claude-test",
            "global_rate_limit_per_hour": "2",
        },
    )

    # Push all three before waiting on any of them, so the watch loop spawns
    # all three process_message() tasks with none yet completed.
    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="hi1"))
    await harness.imsg.push(make_message(rowid=2, guid="g2", chat_id=2, text="hi2"))
    await harness.imsg.push(make_message(rowid=3, guid="g3", chat_id=3, text="hi3"))

    row1 = await wait_for_processed(harness.database, "g1")
    row2 = await wait_for_processed(harness.database, "g2")
    row3 = await wait_for_processed(harness.database, "g3")

    statuses = sorted([row1["status"], row2["status"], row3["status"]])
    assert statuses == ["replied", "replied", "skipped:rate_limit"]
    assert len(harness.imsg.sent_messages) == 2


async def test_quiet_hours_spanning_now_skips(daemon_harness_factory):
    now = datetime.now().astimezone()
    start = (now - timedelta(minutes=2)).strftime("%H:%M")
    end = (now + timedelta(minutes=2)).strftime("%H:%M")

    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={
            "selected_model_id": "claude-test",
            "quiet_hours_start": start,
            "quiet_hours_end": end,
        },
    )
    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi during quiet hours")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "skipped:quiet_hours"
    assert harness.imsg.sent_messages == []


# -- 7. API failures / auto-pause -------------------------------------------------


async def test_repeated_anthropic_failures_trigger_auto_pause(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={
            "selected_model_id": "claude-test",
            "failure_pause_threshold": "3",
        },
    )
    harness.anthropic.fail_always(AnthropicUnavailableError("connection refused"))

    for i in range(1, 4):
        await harness.imsg.push(make_message(rowid=i, guid=f"g{i}", chat_id=1, text=f"hi {i}"))
        row = await wait_for_processed(harness.database, f"g{i}")
        assert row["status"] == "failed"
        assert row["error_code"] == "ANTHROPIC_ERROR"

    assert harness.database.get_settings().paused is False

    # 4th message: 3 consecutive failures already on record -> auto-pause.
    await harness.imsg.push(make_message(rowid=4, guid="g4", chat_id=1, text="hi 4"))
    row4 = await wait_for_processed(harness.database, "g4")
    assert row4["status"] == "skipped:auto_paused"
    assert harness.database.get_settings().paused is True
    assert harness.imsg.sent_messages == []


async def test_imsg_send_failure_records_failed_and_daemon_keeps_serving(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    harness.imsg.send_should_fail = True

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "failed"
    assert row["error_code"] == "SEND_FAILED"
    assert harness.imsg.sent_messages == []

    # Daemon must not have crashed: socket still answers requests.
    client = harness.client()
    result = await client.request("ping")
    assert result == {}
    status = await client.request("status")
    assert status["running"] is True
    await client.close()


# -- 8. anthropic timeout --------------------------------------------------------


async def test_anthropic_timeout_message_maps_to_timeout_error_code(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    harness.anthropic.fail_always(AnthropicUnavailableError("timeout: request took too long"))

    msg = make_message(rowid=1, guid="g1", chat_id=1, text="hi")
    await harness.imsg.push(msg)

    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "failed"
    assert row["error_code"] == "ANTHROPIC_TIMEOUT"
