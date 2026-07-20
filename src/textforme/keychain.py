"""macOS Keychain storage for the Anthropic API key. Owner: Agent 6.

Uses the `security` CLI (generic passwords, service config.KEYCHAIN_SERVICE,
account config.KEYCHAIN_ACCOUNT). The key value must never be logged, printed,
or passed on a visible command line in a way that leaks (use `-w` with the
value as an argument to `security add-generic-password` via subprocess list —
not shell — and never echo it).
"""

from __future__ import annotations

import subprocess

from . import config


def get_api_key() -> str | None:
    """Return the stored key, or None if absent."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", config.KEYCHAIN_SERVICE, "-a", config.KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.rstrip('\n')
    except Exception:
        return None


def set_api_key(key: str) -> None:
    """Store/replace the key (delete-then-add or -U update)."""
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", config.KEYCHAIN_SERVICE, "-a", config.KEYCHAIN_ACCOUNT, "-w", key],
        capture_output=True,
    )


def delete_api_key() -> None:
    """Remove the key; no error if absent."""
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", config.KEYCHAIN_SERVICE, "-a", config.KEYCHAIN_ACCOUNT],
            capture_output=True,
        )
    except Exception:
        pass


def has_api_key() -> bool:
    return get_api_key() is not None
