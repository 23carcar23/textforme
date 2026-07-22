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


async def test_disable_during_reply_timer_window_aborts_send(daemon_harness_factory, monkeypatch):
    """When the reply timer is on, disabling the contact before the countdown
    fires must abort the batched send: _fire_reply re-checks policy."""
    import textforme.daemon as daemon_module

    # A short-but-nonzero countdown gives the socket call time to flip the
    # contact off before the timer fires.
    monkeypatch.setattr(daemon_module.random, "uniform", lambda a, b: 0.4)
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    harness.database.set_contact_reply_timer("c1", True)
    client = harness.client()

    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="hi"))
    # The countdown is now running for this chat; flip the contact off first.
    await asyncio.sleep(0.1)
    result = await client.request("contacts.set_ai", {"chat_guid": "c1", "enabled": False})
    assert result == {}

    # Give the timer time to fire and re-check policy.
    await asyncio.sleep(0.6)
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
    assert alice_after["reply_timer_enabled"] is False

    # Toggle the realistic-texting reply timer over the socket.
    await client.request("contacts.set_reply_timer", {"chat_guid": "c1", "enabled": True})
    contacts_timer = await client.request("contacts.list")
    alice_timer = next(c for c in contacts_timer["contacts"] if c["chat_guid"] == "c1")
    assert alice_timer["reply_timer_enabled"] is True

    settings_before = await client.request("settings.get")
    assert settings_before["settings"]["context_message_limit"] == "10"

    await client.request("settings.set", {"key": "context_message_limit", "value": "25"})
    settings_after = await client.request("settings.get")
    assert settings_after["settings"]["context_message_limit"] == "25"

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
        await client.request("settings.set", {"key": "context_message_limit", "value": "abc"})
    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("settings.set", {"key": "failure_pause_threshold", "value": "3x"})

    # Value unchanged, settings still readable, daemon still healthy.
    result = await client.request("settings.get")
    assert result["settings"]["context_message_limit"] == "10"
    assert harness.database.get_settings().context_message_limit == 10

    # A valid value is still accepted.
    assert await client.request("settings.set", {"key": "context_message_limit", "value": "25"}) == {}
    assert harness.database.get_settings().context_message_limit == 25

    # And the pipeline still runs end-to-end after the rejected writes.
    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="still works?"))
    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "replied"

    await client.close()


# -- contact descriptions over the socket ---------------------------------------


async def test_set_description_via_socket_reaches_system_prompt(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1, ai_enabled=True)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    result = await client.request(
        "contacts.set_description",
        {"chat_guid": "c1", "description": "my very strict mom so be nice to her"},
    )
    assert result == {}

    listed = await client.request("contacts.list")
    by_guid = {c["chat_guid"]: c for c in listed["contacts"]}
    assert by_guid["c1"]["description"] == "my very strict mom so be nice to her"

    await harness.imsg.push(make_message(rowid=1, guid="g1", chat_id=1, text="hi mom's phone"))
    row = await wait_for_processed(harness.database, "g1")
    assert row["status"] == "replied"
    assert "my very strict mom so be nice to her" in harness.anthropic.calls[0]["system"]

    await client.close()


async def test_set_description_validation_errors(daemon_harness_factory):
    harness = await daemon_harness_factory(
        contacts=[make_contact(chat_guid="c1", chat_id=1)],
        settings={"selected_model_id": "claude-test"},
    )
    client = harness.client()

    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("contacts.set_description", {"chat_guid": "missing", "description": "x"})
    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("contacts.set_description", {"chat_guid": "c1", "description": "x" * 2001})
    with pytest.raises(RuntimeError, match="BAD_PARAMS"):
        await client.request("contacts.set_description", {"chat_guid": "c1"})

    # A valid save still works afterwards.
    assert await client.request(
        "contacts.set_description", {"chat_guid": "c1", "description": "likes fishing"}
    ) == {}

    await client.close()
