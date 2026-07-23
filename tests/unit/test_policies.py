"""Unit tests for the policy layer (src/textforme/service/policies.py).

Covers ARCHITECTURE §6 steps 5-10 in order, plus validate_reply edge cases.
"""

from __future__ import annotations

import pytest

from textforme import config
from textforme.config import Settings
from textforme.database import ContactRecord
from textforme.messaging.events import ReplyValidationError, SkipReason
from textforme.service.policies import Decision, PolicyInputs, evaluate, validate_reply


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


def make_inputs(**overrides) -> PolicyInputs:
    defaults = dict(
        contact=make_contact(),
        settings=make_settings(),
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


def test_cooldown_skips_when_recent():
    decision = evaluate(
        make_inputs(seconds_since_last_reply=config.REPLY_COOLDOWN_SECONDS - 0.1)
    )
    assert decision == Decision(allowed=False, skip_reason=SkipReason.COOLDOWN)


def test_cooldown_allows_when_elapsed():
    decision = evaluate(
        make_inputs(seconds_since_last_reply=config.REPLY_COOLDOWN_SECONDS + 0.1)
    )
    assert decision.allowed is True


def test_cooldown_none_allows():
    # No reply has ever been sent to this chat this daemon lifetime.
    decision = evaluate(make_inputs(seconds_since_last_reply=None))
    assert decision.allowed is True


def test_contact_off_wins_over_cooldown():
    decision = evaluate(
        make_inputs(
            contact=make_contact(ai_enabled=False),
            seconds_since_last_reply=0.0,
        )
    )
    assert decision.skip_reason == SkipReason.CONTACT_OFF


def test_cooldown_wins_over_auto_pause():
    settings = make_settings(failure_pause_threshold=1)
    decision = evaluate(
        make_inputs(
            settings=settings,
            consecutive_failures=5,
            seconds_since_last_reply=0.0,
        )
    )
    assert decision.skip_reason == SkipReason.COOLDOWN


def test_contact_off_wins_over_auto_pause():
    # Contact-off (checked earlier) must win even when failures also exceed the
    # auto-pause threshold.
    settings = make_settings(failure_pause_threshold=1)
    decision = evaluate(
        make_inputs(
            contact=make_contact(ai_enabled=False),
            settings=settings,
            consecutive_failures=5,
        )
    )
    assert decision.skip_reason == SkipReason.CONTACT_OFF


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
