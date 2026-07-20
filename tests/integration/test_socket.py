"""Unix-socket control-protocol integration tests (ARCHITECTURE.md §5), driven
through a real running Daemon and (where noted) the real tui.app.DaemonClient.

Owner: Agent 7 (testing).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_contact, make_message, wait_for_processed


# -- 2. enabling via the socket, then a new message gets a reply -----------------


async def test_enable_contact_via_socket_then_message_gets_reply(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=False)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    # Disabled -> no reply.
    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="before enabling"))
    row1 = await wait_for_processed(harness.database, "g1")
    assert row1["status"] == "skipped:contact_off"

    # Enable via the real socket protocol.
    result = await client.request("contacts.set_ai", {"chat_guid": "c1", "enabled": True})
    assert result == {}

    # Now a fresh message should get a reply.
    await harness.imsg.push(make_message(rowid=2, guid="g2", chat_id=1, text="after enabling"))
    row2 = await wait_for_processed(harness.database, "g2")
    assert row2["status"] == "replied"
    assert harness.imsg.sent_messages == [{"chat_id": 1, "text": harness.anthropic.default_reply}]

    await client.close()


async def test_disable_mid_response_delay_aborts_send(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test", "response_delay_seconds": "1"},
    )
    client = harness.client()

    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="hi"))
    # The daemon is now inside its 1s response_delay sleep for this message;
    # flip the contact off before the delay elapses.
    await asyncio.sleep(0.1)
    result = await client.request("contacts.set_ai", {"chat_guid": "c1", "enabled": False})
    assert result == {}

    row = await wait_for_processed(harness.database, "g1", timeout=3.0)
    assert row["status"] == "skipped:contact_off"
    assert harness.imsg.sent_messages == []
    assert harness.anthropic.calls == []

    await client.close()


# -- 4. global pause ------------------------------------------------------------


async def test_global_pause_then_resume_via_socket(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    await client.request("service.pause")
    assert harness.database.get_settings().paused is True

    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="while paused"))
    row1 = await wait_for_processed(harness.database, "g1")
    assert row1["status"] == "skipped:paused"
    assert harness.imsg.sent_messages == []

    await client.request("service.resume")
    assert harness.database.get_settings().paused is False

    await harness.imsg.push(make_message(rowid=2, guid="g2", chat_id=1, text="after resume"))
    row2 = await wait_for_processed(harness.database, "g2")
    assert row2["status"] == "replied"
    assert len(harness.imsg.sent_messages) == 1

    await client.close()


# -- 5. group toggle rejected over the real socket -------------------------------


async def test_group_chat_set_ai_rejected_over_real_socket(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="grp1", chat_id=1, is_group=True, ai_enabled=False)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    with pytest.raises(RuntimeError, match="GROUP_FORBIDDEN"):
        await client.request("contacts.set_ai", {"chat_guid": "grp1", "enabled": True})

    contacts = await client.request("contacts.list")
    assert contacts["contacts"][0]["ai_enabled"] is False

    await client.close()


# -- 9. tui.app.DaemonClient full round trip against a real daemon --------------


async def test_tui_daemon_client_full_round_trip(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, display_name="Alice", ai_enabled=False)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    status = await client.request("status")
    assert status["running"] is True
    assert status["model_id"] == "claude-test"

    contacts_before = await client.request("contacts.list")
    alice = next(c for c in contacts_before["contacts"] if c["chat_guid"] == "c1")
    assert alice["ai_enabled"] is False

    await client.request("contacts.set_ai", {"chat_guid": "c1", "enabled": True})
    contacts_after = await client.request("contacts.list")
    alice_after = next(c for c in contacts_after["contacts"] if c["chat_guid"] == "c1")
    assert alice_after["ai_enabled"] is True

    settings_before = await client.request("settings.get")
    assert settings_before["settings"]["maximum_reply_length"] == "300"

    await client.request("settings.set", {"key": "maximum_reply_length", "value": "150"})
    settings_after = await client.request("settings.get")
    assert settings_after["settings"]["maximum_reply_length"] == "150"

    with pytest.raises(RuntimeError, match="UNKNOWN_KEY"):
        await client.request("settings.set", {"key": "not_a_real_setting", "value": "x"})

    await client.close()


async def test_settings_set_rejects_uncoercible_numeric_value(daemon_harness_factory):
    """A malformed numeric value must be rejected with BAD_PARAMS instead of
    being persisted (where it would make every later get_settings() raise and
    silently break the pipeline)."""
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("settings.set", {"key": "maximum_reply_length", "value": "abc"})
    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("settings.set", {"key": "response_delay_seconds", "value": "3s"})

    # Value unchanged, settings still readable, daemon still healthy.
    result = await client.request("settings.get")
    assert result["settings"]["maximum_reply_length"] == "300"
    assert harness.database.get_settings().maximum_reply_length == 300

    # A valid value is still accepted.
    assert await client.request("settings.set", {"key": "maximum_reply_length", "value": "150"}) == {}
    assert harness.database.get_settings().maximum_reply_length == 150

    # And the pipeline still runs end-to-end after the rejected writes.
    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="still works?"))
    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "replied"

    await client.close()
