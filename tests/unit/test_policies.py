"""Unit tests for the policy layer (src/textforme/service/policies.py).

Covers ARCHITECTURE §6 steps 5-12 in order, plus in_quiet_hours and
validate_reply edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from textforme.config import Settings
from textforme.database import ContactRecord
from textforme.messaging.events import ReplyValidationError, SkipReason
from textforme.service.policies import Decision, PolicyInputs, evaluate, in_quiet_hours, validate_reply


def make_contact(is_group: bool = False, ai_enabled: bool = True) -> ContactRecord:
    return ContactRecord(
        chat_guid="guid-1",
        chat_id=1,
        display_name="Alice",
        address="+15551234567",
        service="iMessage",
        is_group=is_group,
        ai_enabled=ai_enabled,
    )


def make_settings(**overrides) -> Settings:
    base = Settings()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_inputs(**overrides) -> PolicyInputs:
    defaults = dict(
        contact=make_contact(),
        settings=make_settings(),
        now=NOW,
        replies_last_hour=0,
        last_reply_at=None,
        consecutive_failures=0,
    )
    defaults.update(overrides)
    return PolicyInputs(**defaults)


# -- evaluate(): ordered checks ----------------------------------------------


def test_unknown_contact_skips_first():
    decision = evaluate(make_inputs(contact=None))
    assert decision == Decision(allowed=False, skip_reason=SkipReason.UNKNOWN_CONTACT)


def test_group_chat_skips():
    decision = evaluate(make_inputs(contact=make_contact(is_group=True)))
    assert decision == Decision(allowed=False, skip_reason=SkipReason.GROUP)


def test_paused_wins_over_contact_off():
    # Even if contact_off would also trigger, paused (checked earlier) must win.
    decision = evaluate(
        make_inputs(
            contact=make_contact(ai_enabled=False),
            settings=make_settings(paused=True),
        )
    )
    assert decision.skip_reason == SkipReason.PAUSED


def test_global_off_skips():
    decision = evaluate(make_inputs(settings=make_settings(global_ai_enabled=False)))
    assert decision.skip_reason == SkipReason.GLOBAL_OFF


def test_global_off_wins_over_contact_off():
    decision = evaluate(
        make_inputs(
            contact=make_contact(ai_enabled=False),
            settings=make_settings(global_ai_enabled=False),
        )
    )
    assert decision.skip_reason == SkipReason.GLOBAL_OFF


def test_contact_off_skips():
    decision = evaluate(make_inputs(contact=make_contact(ai_enabled=False)))
    assert decision.skip_reason == SkipReason.CONTACT_OFF


def test_quiet_hours_skips():
    settings = make_settings(quiet_hours_start="22:00", quiet_hours_end="07:00")
    now = NOW.replace(hour=23, minute=0)
    decision = evaluate(make_inputs(settings=settings, now=now))
    assert decision.skip_reason == SkipReason.QUIET_HOURS


def test_quiet_hours_wins_over_cooldown_and_rate_limit():
    settings = make_settings(
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
        contact_cooldown_seconds=60,
        global_rate_limit_per_hour=1,
    )
    now = NOW.replace(hour=23, minute=0)
    decision = evaluate(
        make_inputs(
            settings=settings,
            now=now,
            last_reply_at=now.astimezone(timezone.utc),
            replies_last_hour=5,
        )
    )
    assert decision.skip_reason == SkipReason.QUIET_HOURS


def test_cooldown_skips_when_recent_reply():
    settings = make_settings(contact_cooldown_seconds=60)
    last_reply = NOW - timedelta(seconds=10)
    decision = evaluate(make_inputs(settings=settings, last_reply_at=last_reply))
    assert decision.skip_reason == SkipReason.COOLDOWN


def test_cooldown_boundary_exact_threshold_allows():
    # elapsed == cooldown_seconds is NOT < cooldown_seconds -> not a cooldown skip.
    settings = make_settings(contact_cooldown_seconds=60)
    last_reply = NOW - timedelta(seconds=60)
    decision = evaluate(make_inputs(settings=settings, last_reply_at=last_reply))
    assert decision.allowed is True


def test_cooldown_boundary_just_under_threshold_skips():
    settings = make_settings(contact_cooldown_seconds=60)
    last_reply = NOW - timedelta(seconds=59, milliseconds=999)
    decision = evaluate(make_inputs(settings=settings, last_reply_at=last_reply))
    assert decision.skip_reason == SkipReason.COOLDOWN


def test_no_last_reply_at_skips_cooldown_check():
    settings = make_settings(contact_cooldown_seconds=999999)
    decision = evaluate(make_inputs(settings=settings, last_reply_at=None))
    assert decision.allowed is True


def test_rate_limit_skips():
    settings = make_settings(global_rate_limit_per_hour=3)
    decision = evaluate(make_inputs(settings=settings, replies_last_hour=3))
    assert decision.skip_reason == SkipReason.RATE_LIMIT


def test_rate_limit_boundary_below_allows():
    settings = make_settings(global_rate_limit_per_hour=3)
    decision = evaluate(make_inputs(settings=settings, replies_last_hour=2))
    assert decision.allowed is True


def test_rate_limit_wins_over_auto_pause_check_order():
    # Rate limit is checked (step 11) before auto-pause (step 12); ensure a
    # rate-limited request is reported as RATE_LIMIT even if failures also
    # exceed the auto-pause threshold.
    settings = make_settings(global_rate_limit_per_hour=1, failure_pause_threshold=1)
    decision = evaluate(
        make_inputs(settings=settings, replies_last_hour=1, consecutive_failures=5)
    )
    assert decision.skip_reason == SkipReason.RATE_LIMIT


def test_auto_pause_triggers():
    settings = make_settings(failure_pause_threshold=3)
    decision = evaluate(make_inputs(settings=settings, consecutive_failures=3))
    assert decision == Decision(
        allowed=False, skip_reason=SkipReason.AUTO_PAUSED, trigger_auto_pause=True
    )


def test_auto_pause_boundary_below_threshold_allows():
    settings = make_settings(failure_pause_threshold=3)
    decision = evaluate(make_inputs(settings=settings, consecutive_failures=2))
    assert decision.allowed is True


def test_auto_pause_disabled_when_threshold_zero():
    settings = make_settings(failure_pause_threshold=0)
    decision = evaluate(make_inputs(settings=settings, consecutive_failures=100))
    assert decision.allowed is True


def test_all_checks_pass_allows():
    decision = evaluate(make_inputs())
    assert decision == Decision(allowed=True)


# -- in_quiet_hours -----------------------------------------------------------


def test_quiet_hours_empty_start_disabled():
    assert in_quiet_hours(NOW, "", "07:00") is False


def test_quiet_hours_empty_end_disabled():
    assert in_quiet_hours(NOW, "22:00", "") is False


def test_quiet_hours_both_empty_disabled():
    assert in_quiet_hours(NOW, "", "") is False


def test_quiet_hours_same_day_range_inside():
    now = NOW.replace(hour=13, minute=0)
    assert in_quiet_hours(now, "12:00", "14:00") is True


def test_quiet_hours_same_day_range_outside():
    now = NOW.replace(hour=15, minute=0)
    assert in_quiet_hours(now, "12:00", "14:00") is False


def test_quiet_hours_same_day_start_boundary_inclusive():
    now = NOW.replace(hour=12, minute=0)
    assert in_quiet_hours(now, "12:00", "14:00") is True


def test_quiet_hours_same_day_end_boundary_exclusive():
    now = NOW.replace(hour=14, minute=0)
    assert in_quiet_hours(now, "12:00", "14:00") is False


def test_quiet_hours_crossing_midnight_late_night():
    now = NOW.replace(hour=23, minute=30)
    assert in_quiet_hours(now, "22:00", "07:00") is True


def test_quiet_hours_crossing_midnight_early_morning():
    now = NOW.replace(hour=3, minute=0)
    assert in_quiet_hours(now, "22:00", "07:00") is True


def test_quiet_hours_crossing_midnight_daytime_not_quiet():
    now = NOW.replace(hour=12, minute=0)
    assert in_quiet_hours(now, "22:00", "07:00") is False


def test_quiet_hours_crossing_midnight_start_boundary_inclusive():
    now = NOW.replace(hour=22, minute=0)
    assert in_quiet_hours(now, "22:00", "07:00") is True


def test_quiet_hours_crossing_midnight_end_boundary_exclusive():
    now = NOW.replace(hour=7, minute=0)
    assert in_quiet_hours(now, "22:00", "07:00") is False


def test_quiet_hours_invalid_format_returns_false():
    assert in_quiet_hours(NOW, "not-a-time", "07:00") is False
    assert in_quiet_hours(NOW, "22:00", "garbage") is False


# -- validate_reply -----------------------------------------------------------


def test_validate_reply_strips_whitespace():
    assert validate_reply("  hello there  ", 100) == "hello there"


def test_validate_reply_strips_control_chars():
    text = "hello\x00\x01 world\x07"
    assert validate_reply(text, 100) == "hello world"


def test_validate_reply_preserves_newlines_and_tabs():
    text = "line one\nline two\tend"
    assert validate_reply(text, 100) == "line one\nline two\tend"


def test_validate_reply_normalizes_crlf():
    text = "line one\r\nline two"
    result = validate_reply(text, 100)
    assert "\r" not in result
    assert result == "line one\nline two"


def test_validate_reply_empty_raises():
    with pytest.raises(ReplyValidationError):
        validate_reply("   ", 100)


def test_validate_reply_only_control_chars_raises():
    with pytest.raises(ReplyValidationError):
        validate_reply("\x00\x01\x02", 100)


def test_validate_reply_under_limit_unchanged():
    text = "short reply"
    assert validate_reply(text, 100) == text


def test_validate_reply_truncates_at_word_boundary():
    # max_chars=20; last space within the cut is well past 60% of max_chars.
    text = "this is a long message that keeps going"
    result = validate_reply(text, 20)
    assert len(result) <= 20
    assert result == "this is a long"


def test_validate_reply_hard_cuts_when_no_good_word_boundary():
    # A single long token with no spaces before max_chars * 0.6.
    text = "a" * 50
    result = validate_reply(text, 20)
    assert result == "a" * 20


def test_validate_reply_exact_length_unchanged():
    text = "x" * 20
    assert validate_reply(text, 20) == text
