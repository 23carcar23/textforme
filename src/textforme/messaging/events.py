"""Shared error types, status codes, and skip reasons. FROZEN contract."""

from __future__ import annotations

from enum import StrEnum


class ImsgError(Exception):
    """Base error for the imsg adapter."""


class ImsgUnavailableError(ImsgError):
    """imsg binary missing, process dead, or not restartable right now."""


class ImsgProtocolError(ImsgError):
    """Malformed / unexpected JSON-RPC traffic."""


class ImsgRequestError(ImsgError):
    """imsg returned a JSON-RPC error for a request."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"imsg error {code}: {message}")
        self.code = code
        self.message = message


class AnthropicUnavailableError(Exception):
    """Anthropic API failed after retries/timeout."""


class ReplyValidationError(Exception):
    """Generated reply failed validation and must not be sent."""


class ErrorCode(StrEnum):
    """error_code values recorded in processed_messages."""

    IMSG_UNAVAILABLE = "IMSG_UNAVAILABLE"
    ANTHROPIC_TIMEOUT = "ANTHROPIC_TIMEOUT"
    ANTHROPIC_ERROR = "ANTHROPIC_ERROR"
    SEND_FAILED = "SEND_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NO_API_KEY = "NO_API_KEY"
    NO_MODEL = "NO_MODEL"
    INTERNAL = "INTERNAL"


class SkipReason(StrEnum):
    """Reasons a message is recorded as skipped:<reason>."""

    GROUP = "group"
    PAUSED = "paused"
    GLOBAL_OFF = "global_off"
    CONTACT_OFF = "contact_off"
    UNKNOWN_CONTACT = "unknown_contact"
    AUTO_PAUSED = "auto_paused"
    COOLDOWN = "cooldown"


class ProcessStatus(StrEnum):
    REPLIED = "replied"
    FAILED = "failed"
    SKIPPED = "skipped"  # stored as "skipped:<reason>"
