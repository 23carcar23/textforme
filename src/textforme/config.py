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

# All settings are stored as strings in SQLite; Settings handles typing.
DEFAULT_SETTINGS: dict[str, str] = {
    "selected_model_id": "",
    "global_ai_enabled": "true",
    "paused": "false",
    "maximum_reply_length": "300",
    "response_delay_seconds": "3",
    "context_message_limit": "10",
    "quiet_hours_start": "",  # "HH:MM" local time, empty = disabled
    "quiet_hours_end": "",
    "global_rate_limit_per_hour": "20",
    "contact_cooldown_seconds": "60",
    "failure_pause_threshold": "5",
    "last_seen_rowid": "0",
    "onboarding_complete": "false",
}


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Typed view over the settings table."""

    selected_model_id: str = ""
    global_ai_enabled: bool = True
    paused: bool = False
    maximum_reply_length: int = 300
    response_delay_seconds: float = 3.0
    context_message_limit: int = 10
    quiet_hours_start: str = ""
    quiet_hours_end: str = ""
    global_rate_limit_per_hour: int = 20
    contact_cooldown_seconds: int = 60
    failure_pause_threshold: int = 5
    last_seen_rowid: int = 0
    onboarding_complete: bool = False

    @classmethod
    def from_mapping(cls, raw: dict[str, str]) -> "Settings":
        merged = {**DEFAULT_SETTINGS, **raw}
        return cls(
            selected_model_id=merged["selected_model_id"],
            global_ai_enabled=_to_bool(merged["global_ai_enabled"]),
            paused=_to_bool(merged["paused"]),
            maximum_reply_length=int(merged["maximum_reply_length"] or 300),
            response_delay_seconds=float(merged["response_delay_seconds"] or 3),
            context_message_limit=int(merged["context_message_limit"] or 10),
            quiet_hours_start=merged["quiet_hours_start"],
            quiet_hours_end=merged["quiet_hours_end"],
            global_rate_limit_per_hour=int(merged["global_rate_limit_per_hour"] or 20),
            contact_cooldown_seconds=int(merged["contact_cooldown_seconds"] or 60),
            failure_pause_threshold=int(merged["failure_pause_threshold"] or 5),
            last_seen_rowid=int(merged["last_seen_rowid"] or 0),
            onboarding_complete=_to_bool(merged["onboarding_complete"]),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "selected_model_id": self.selected_model_id,
            "global_ai_enabled": str(self.global_ai_enabled).lower(),
            "paused": str(self.paused).lower(),
            "maximum_reply_length": str(self.maximum_reply_length),
            "response_delay_seconds": str(self.response_delay_seconds),
            "context_message_limit": str(self.context_message_limit),
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "global_rate_limit_per_hour": str(self.global_rate_limit_per_hour),
            "contact_cooldown_seconds": str(self.contact_cooldown_seconds),
            "failure_pause_threshold": str(self.failure_pause_threshold),
            "last_seen_rowid": str(self.last_seen_rowid),
            "onboarding_complete": str(self.onboarding_complete).lower(),
        }


def ensure_dirs() -> None:
    """Create app-support and log directories with user-only permissions."""
    for path in (APP_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
    os.chmod(APP_DIR, 0o700)
