"""Policy layer — the ONLY authorizer of outgoing replies. Owner: Agent 4.

Implements pipeline steps 5–12 of ARCHITECTURE §6 as a pure decision function
over inputs the daemon fetches, plus reply validation. Opus-reviewed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..config import Settings
from ..database import ContactRecord
from ..messaging.events import ReplyValidationError, SkipReason


@dataclass
class PolicyInputs:
    """Snapshot the daemon assembles before deciding."""

    contact: ContactRecord | None
    settings: Settings
    consecutive_failures: int


@dataclass
class Decision:
    allowed: bool
    skip_reason: SkipReason | None = None
    trigger_auto_pause: bool = False  # daemon must set paused=true when set


def evaluate(inputs: PolicyInputs) -> Decision:
    """Ordered checks: unknown contact, group, paused, global off, contact off,
    auto-pause threshold."""
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

    if settings.failure_pause_threshold > 0 and (
        inputs.consecutive_failures >= settings.failure_pause_threshold
    ):
        return Decision(
            allowed=False,
            skip_reason=SkipReason.AUTO_PAUSED,
            trigger_auto_pause=True,
        )

    return Decision(allowed=True)


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
