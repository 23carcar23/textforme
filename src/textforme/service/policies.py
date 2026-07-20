"""Policy layer — the ONLY authorizer of outgoing replies. Owner: Agent 4.

Implements pipeline steps 5–12 of ARCHITECTURE §6 as a pure decision function
over inputs the daemon fetches, plus reply validation. Opus-reviewed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings
from ..database import ContactRecord
from ..messaging.events import ReplyValidationError, SkipReason


@dataclass
class PolicyInputs:
    """Snapshot the daemon assembles before deciding."""

    contact: ContactRecord | None
    settings: Settings
    now: datetime  # aware, local tz (quiet hours are local)
    replies_last_hour: int
    last_reply_at: datetime | None  # aware UTC, for this chat
    consecutive_failures: int


@dataclass
class Decision:
    allowed: bool
    skip_reason: SkipReason | None = None
    trigger_auto_pause: bool = False  # daemon must set paused=true when set


def evaluate(inputs: PolicyInputs) -> Decision:
    """Ordered checks: unknown contact, group, paused, global off, contact off,
    quiet hours, cooldown, rate limit, auto-pause threshold."""
    settings = inputs.settings

    if inputs.contact is None:
        return Decision(allowed=False, skip_reason=SkipReason.UNKNOWN_CONTACT)

    if inputs.contact.is_group:
        return Decision(allowed=False, skip_reason=SkipReason.GROUP)

    if settings.paused:
        return Decision(allowed=False, skip_reason=SkipReason.PAUSED)

    if not settings.global_ai_enabled:
        return Decision(allowed=False, skip_reason=SkipReason.GLOBAL_OFF)

    if not inputs.contact.ai_enabled:
        return Decision(allowed=False, skip_reason=SkipReason.CONTACT_OFF)

    if in_quiet_hours(inputs.now, settings.quiet_hours_start, settings.quiet_hours_end):
        return Decision(allowed=False, skip_reason=SkipReason.QUIET_HOURS)

    if inputs.last_reply_at is not None:
        now_utc = inputs.now.astimezone(timezone.utc)
        elapsed = (now_utc - inputs.last_reply_at).total_seconds()
        if elapsed < settings.contact_cooldown_seconds:
            return Decision(allowed=False, skip_reason=SkipReason.COOLDOWN)

    if inputs.replies_last_hour >= settings.global_rate_limit_per_hour:
        return Decision(allowed=False, skip_reason=SkipReason.RATE_LIMIT)

    if settings.failure_pause_threshold > 0 and (
        inputs.consecutive_failures >= settings.failure_pause_threshold
    ):
        return Decision(
            allowed=False,
            skip_reason=SkipReason.AUTO_PAUSED,
            trigger_auto_pause=True,
        )

    return Decision(allowed=True)


def in_quiet_hours(now_local: datetime, start: str, end: str) -> bool:
    """start/end 'HH:MM'; empty either → False. Handles ranges crossing midnight
    (e.g. 22:00–07:00)."""
    if not start or not end:
        return False

    try:
        start_h, start_m = start.split(":")
        end_h, end_m = end.split(":")
        start_minutes = int(start_h) * 60 + int(start_m)
        end_minutes = int(end_h) * 60 + int(end_m)
    except (ValueError, TypeError):
        return False

    now_minutes = now_local.hour * 60 + now_local.minute

    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    # Range crosses midnight.
    return now_minutes >= start_minutes or now_minutes < end_minutes


def validate_reply(text: str, max_chars: int) -> str:
    """Strip control chars & surrounding whitespace; truncate to max_chars at a
    word boundary where possible. Raises ReplyValidationError if empty."""
    normalized = text.replace("\r\n", "\n")
    cleaned = "".join(
        ch for ch in normalized if ch in ("\n", "\t") or unicodedata.category(ch) != "Cc"
    )
    cleaned = cleaned.strip()

    if not cleaned:
        raise ReplyValidationError("reply is empty after validation")

    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars]
        last_space = cut.rfind(" ")
        if last_space > max_chars * 0.6:
            cut = cut[:last_space]
        cleaned = cut.rstrip()

    if not cleaned:
        raise ReplyValidationError("reply is empty after truncation")

    return cleaned
