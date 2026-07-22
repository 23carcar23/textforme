"""Paths, constants, settings typing. FROZEN contract — see docs/ARCHITECTURE.md."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "TextForMe"

APP_DIR = Path.home() / "Library" / "Application Support" / "TextForMe"
DB_PATH = APP_DIR / "textforme.db"
SOCKET_PATH = APP_DIR / "daemon.sock"
LOG_DIR = Path.home() / "Library" / "Logs" / "TextForMe"

LAUNCH_AGENT_LABEL = "com.textforme.daemon"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

KEYCHAIN_SERVICE = "TextForMe"
KEYCHAIN_ACCOUNT = "anthropic-api-key"

# Upper bound for the owner-written per-contact note and the custom prompt
# fields. Large enough for full paragraphs, small enough to stay well under
# the daemon socket's line-read limit.
MAX_CONTACT_NOTE_CHARS = 2000
MAX_PROMPT_CHARS = 6000

# Fixed upper bound applied to every generated reply. Formerly the owner-facing
# "maximum_reply_length" setting; now an internal constant so replies stay
# text-message-sized without exposing a knob.
MAX_REPLY_CHARS = 300

# "Realistic texting" reply timer: when a contact has the reply timer enabled,
# the first message of a burst starts a random countdown between 0 and this
# many seconds (0–3 minutes); the batched reply fires when it expires.
REPLY_TIMER_MAX_SECONDS = 180

# The context_message_limit value the "unlimited" UI option maps to — a high
# but finite cap on how many recent messages are pulled for context.
UNLIMITED_CONTEXT_LIMIT = 1000


def parse_context_limit(value: str) -> int:
    """Coerce a context_message_limit setting string to an int.

    The special value "unlimited" maps to UNLIMITED_CONTEXT_LIMIT; anything
    else must be a plain integer (empty falls back to the default of 10).
    """
    if value.strip().lower() == "unlimited":
        return UNLIMITED_CONTEXT_LIMIT
    return int(value or 10)


# All settings are stored as strings in SQLite; Settings handles typing.
DEFAULT_SETTINGS: dict[str, str] = {
    "selected_model_id": "",
    "global_ai_enabled": "true",
    "paused": "false",
    "context_message_limit": "10",
    "failure_pause_threshold": "5",
    "last_seen_rowid": "0",
    "onboarding_complete": "false",
    # ISO 8601 UTC timestamp of the most recent "Brief me" summary. Used to
    # decide whether any new AI conversations exist since the last brief.
    "last_brief_at": "",
    # Owner-authored prompt customization (empty = use built-in defaults).
    # system_prompt overrides the base texting-assistant instructions;
    # persona_prompt describes the owner; style_profile describes how the
    # owner texts. See anthropic/prompts.py for how they are assembled.
    "system_prompt": "",
    "persona_prompt": "",
    "style_profile": "",
}


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Typed view over the settings table."""

    selected_model_id: str = ""
    global_ai_enabled: bool = True
    paused: bool = False
    context_message_limit: int = 10
    failure_pause_threshold: int = 5
    last_seen_rowid: int = 0
    onboarding_complete: bool = False
    last_brief_at: str = ""
    system_prompt: str = ""
    persona_prompt: str = ""
    style_profile: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, str]) -> "Settings":
        merged = {**DEFAULT_SETTINGS, **raw}
        return cls(
            selected_model_id=merged["selected_model_id"],
            global_ai_enabled=_to_bool(merged["global_ai_enabled"]),
            paused=_to_bool(merged["paused"]),
            context_message_limit=parse_context_limit(merged["context_message_limit"]),
            failure_pause_threshold=int(merged["failure_pause_threshold"] or 5),
            last_seen_rowid=int(merged["last_seen_rowid"] or 0),
            onboarding_complete=_to_bool(merged["onboarding_complete"]),
            last_brief_at=merged["last_brief_at"],
            system_prompt=merged["system_prompt"],
            persona_prompt=merged["persona_prompt"],
            style_profile=merged["style_profile"],
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "selected_model_id": self.selected_model_id,
            "global_ai_enabled": str(self.global_ai_enabled).lower(),
            "paused": str(self.paused).lower(),
            "context_message_limit": str(self.context_message_limit),
            "failure_pause_threshold": str(self.failure_pause_threshold),
            "last_seen_rowid": str(self.last_seen_rowid),
            "onboarding_complete": str(self.onboarding_complete).lower(),
            "last_brief_at": self.last_brief_at,
            "system_prompt": self.system_prompt,
            "persona_prompt": self.persona_prompt,
            "style_profile": self.style_profile,
        }


def ensure_dirs() -> None:
    """Create app-support and log directories with user-only permissions."""
    for path in (APP_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
    os.chmod(APP_DIR, 0o700)
