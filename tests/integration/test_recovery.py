"""Dedup + restart-recovery integration tests (ARCHITECTURE.md §6 steps 3, 17).

Owner: Agent 7 (testing).
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import DaemonHarness, FakeAnthropicClient, FakeImsgClient, make_contact, make_message, wait_for_processed


# -- 3a. dedup: same guid delivered twice --------------------------------------


async def test_duplicate_guid_delivered_twice_only_one_send(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )

    msg = make_message(rowid=1, guid="dup-guid", chat_id=1, text="hello")
    await harness.imsg.push(msg)
    row1 = await wait_for_processed(harness.database, "dup-guid")
    assert row1["status"] == "replied"
    assert len(harness.imsg.sent_messages) == 1

    # Re-delivery of the identical notification (e.g. imsg resubscribe replay).
    await harness.imsg.push(msg)
    # Give the (silently-ignored) second delivery a moment to be dropped, then
    # confirm no second send happened and the row is unchanged.
    import asyncio

    await asyncio.sleep(0.2)
    assert len(harness.imsg.sent_messages) == 1
    row_after = await wait_for_processed(harness.database, "dup-guid")
    assert row_after == row1


# -- 3b. restart recovery -------------------------------------------------------


async def test_restart_recovery_no_second_send_and_resubscribes_from_watermark(tmp_path: Path):
    db_path = tmp_path / "restart.db"
    contact = make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)

    # -- first daemon instance: processes one message, then is stopped --------
    harness1 = DaemonHarness(
        db_path=db_path,
        contacts=[contact],
        settings={"selected_model_id": "claude-test"},
    )
    await harness1.start()
    msg = make_message(rowid=42, guid="g42", chat_id=1, text="hello before restart")
    await harness1.imsg.push(msg)
    row1 = await wait_for_processed(harness1.database, "g42")
    assert row1["status"] == "replied"
    assert len(harness1.imsg.sent_messages) == 1
    assert harness1.database.get_settings().last_seen_rowid == 42
    await harness1.stop()  # closes the Database connection too

    # -- second daemon instance: same DB file, fresh fakes ---------------------
    new_imsg = FakeImsgClient()
    new_anthropic = FakeAnthropicClient()
    harness2 = DaemonHarness(
        db_path=db_path,
        imsg=new_imsg,
        anthropic=new_anthropic,
        settings={"selected_model_id": "claude-test"},
    )
    await harness2.start()
    try:
        # The daemon must have resubscribed from the persisted watermark.
        assert new_imsg.watch_subscribe_since_rowids == [42]

        # Replaying the exact same event (as imsg might on reconnect) must be
        # a no-op: guid already recorded as processed.
        await new_imsg.push(msg)
        import asyncio

        await asyncio.sleep(0.2)
        assert new_imsg.sent_messages == []
        assert new_anthropic.calls == []

        row_still = await wait_for_processed(harness2.database, "g42")
        assert row_still["status"] == "replied"
    finally:
        await harness2.stop()
