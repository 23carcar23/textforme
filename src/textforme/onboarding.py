"""First-run onboarding flow. Owner: Agent 3.

Runs in the terminal (plain prompts are fine; Textual optional) when
settings.onboarding_complete is false or no API key exists. Steps (PRD §4):

1. System checks (report each as ✓/✗ with a fix hint; abort on hard failures):
   - macOS 14+ (platform.mac_ver)
   - `imsg` on PATH (hint: brew install steipete/tap/imsg)
   - Messages chat.db readable (~/Library/Messages/chat.db → Full Disk Access hint)
   - Messages signed in / Automation permission: best-effort note (verified by
     the dry test at the end).
2. Masked API-key prompt (getpass — never echoed, never logged/persisted
   outside Keychain). Validate via AnthropicClient.validate_key(); re-prompt on
   failure; store with keychain.set_api_key().
3. Model selection from AnthropicClient.list_models() (live Models API, never
   hard-coded); numbered picker showing display names.
4. Create DB (Database at config.DB_PATH), save selected_model_id, sync
   contacts if daemon not yet available (direct ImsgClient use is allowed here
   only for the initial sync + dry test), set onboarding_complete=true.
5. launchagent.install() + start(); wait briefly for the socket to appear.
6. Dry messaging test: imsg health_check (chats.list) — NO real message sent.
7. Return control to cli.main(), which opens the TUI.

run_onboarding() is sync (wraps asyncio.run internally where needed) and
returns True on success.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import platform
import shutil
import time
from pathlib import Path

from . import config, contact_names, keychain, launchagent
from .anthropic.client import AnthropicClient
from .anthropic.models import ModelInfo
from .database import ContactRecord, Database
from .messaging.client import ImsgClient
from .messaging.events import AnthropicUnavailableError, ImsgUnavailableError
from .tui.app import DaemonClient

_SOCKET_WAIT_SECONDS = 10.0


def needs_onboarding() -> bool:
    """True if the DB doesn't exist yet, onboarding never finished, or no
    Anthropic API key is stored in the Keychain."""
    if not config.DB_PATH.exists():
        return True
    if not keychain.has_api_key():
        return True
    try:
        db = Database(config.DB_PATH)
        try:
            settings = db.get_settings()
        finally:
            db.close()
    except Exception:
        return True
    return not settings.onboarding_complete


# -- terminal helpers -----------------------------------------------------


def _check(label: str, ok: bool, hint: str = "") -> bool:
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {label}")
    if not ok and hint:
        print(f"      -> {hint}")
    return ok


def _check_macos_version() -> bool:
    version_str = platform.mac_ver()[0] or ""
    try:
        major = int(version_str.split(".")[0])
    except (ValueError, IndexError):
        major = 0
    label = f"macOS 14+ (detected {version_str or 'unknown'})"
    return _check(label, major >= 14, "TextForMe requires macOS 14 (Sonoma) or newer.")


def _check_imsg_on_path() -> bool:
    return _check(
        "imsg on PATH",
        shutil.which("imsg") is not None,
        "Install with: brew install steipete/tap/imsg",
    )


def _check_chat_db_readable() -> bool:
    chat_db = Path.home() / "Library" / "Messages" / "chat.db"
    readable = chat_db.exists() and os.access(chat_db, os.R_OK)
    return _check(
        "Messages chat.db readable",
        readable,
        "Grant Full Disk Access: System Settings > Privacy & Security > Full Disk "
        "Access, add your terminal app, then restart it.",
    )


def _run_system_checks() -> bool:
    print("TextForMe setup")
    print()
    print("System checks:")
    macos_ok = _check_macos_version()
    imsg_ok = _check_imsg_on_path()
    chatdb_ok = _check_chat_db_readable()
    print("  [i] Messages sign-in / Automation permission is verified by the dry test below.")
    print()
    return macos_ok and imsg_ok and chatdb_ok


def _prompt_api_key() -> str:
    print("Anthropic API key setup:")
    while True:
        key = getpass.getpass("  API key (input hidden): ").strip()
        if not key:
            print("  API key cannot be empty.")
            continue
        print("  Validating key...")
        client = AnthropicClient(key)
        try:
            valid = asyncio.run(client.validate_key())
        except AnthropicUnavailableError as exc:
            print(f"  Could not reach the Anthropic API ({exc}). Try again.")
            continue
        if not valid:
            print("  That key was rejected by Anthropic. Please try again.")
            continue
        return key


def _pick_model(api_key: str) -> str:
    client = AnthropicClient(api_key)
    models: list[ModelInfo] = []
    try:
        models = asyncio.run(client.list_models())
    except AnthropicUnavailableError as exc:
        print(f"  Could not list models ({exc}).")
    if not models:
        return input("  Enter a model id manually: ").strip()
    print("Choose a model:")
    for index, model in enumerate(models, start=1):
        print(f"  {index}. {model.display_name} ({model.model_id})")
    while True:
        choice = input(f"  Model [1-{len(models)}]: ").strip()
        try:
            selected = int(choice)
        except ValueError:
            print("  Enter a number.")
            continue
        if 1 <= selected <= len(models):
            return models[selected - 1].model_id
        print("  Out of range.")


async def _sync_contacts(db: Database) -> bool:
    """Sync contacts from imsg into the DB. Returns True if imsg is healthy."""
    client = ImsgClient()
    try:
        await client.start()
    except ImsgUnavailableError as exc:
        print(f"  imsg unavailable ({exc}).")
        return False
    try:
        try:
            chats = await client.list_contacts()
        except Exception as exc:  # noqa: BLE001 - best-effort during setup
            print(f"  Could not list contacts ({exc}).")
            chats = []
        # Best-effort local Address Book fallback for chats where imsg had no
        # resolved name (e.g. Contacts permission not granted to `imsg rpc`).
        # Loaded once per sync; degrades to {} on any permission/I-O failure.
        name_map = contact_names.load_contact_names()
        for chat in chats:
            display_name = chat.display_name
            if not display_name and not chat.is_group:
                display_name = contact_names.resolve(chat.address, name_map) or ""
            db.upsert_contact(
                ContactRecord(
                    chat_guid=chat.guid,
                    chat_id=chat.chat_id,
                    display_name=display_name,
                    address=chat.address,
                    service=chat.service,
                    is_group=chat.is_group,
                    ai_enabled=False,
                )
            )
        try:
            return await client.health_check()
        except Exception:  # noqa: BLE001
            return False
    finally:
        await client.stop()


def _wait_for_socket(timeout: float = _SOCKET_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if config.SOCKET_PATH.exists():
            return True
        time.sleep(0.25)
    return config.SOCKET_PATH.exists()


async def _dry_test() -> tuple[bool, dict | None]:
    """Prefer the daemon socket; fall back to a direct ImsgClient health check.

    Returns (ok, daemon_status). daemon_status is the daemon's "status" RPC
    response when the daemon was reachable (so callers can inspect fields
    like chat_db_readable / imsg_ok), or None when the daemon wasn't reachable
    and we fell back to a direct ImsgClient check.
    """
    client = DaemonClient()
    try:
        if await client.connect():
            status = await client.request("status")
            return True, status
    except Exception:  # noqa: BLE001
        pass
    finally:
        await client.close()

    fallback = ImsgClient()
    try:
        await fallback.start()
        return await fallback.health_check(), None
    except Exception:  # noqa: BLE001
        return False, None
    finally:
        try:
            await fallback.stop()
        except Exception:  # noqa: BLE001
            pass


_FDA_WARNING_LINES = (
    "  WARNING: The background service cannot read the Messages database (Full Disk Access).",
    "      -> Grant Full Disk Access in System Settings > Privacy & Security > Full Disk",
    "         Access to your terminal app AND to the real imsg binary — /opt/homebrew/bin/imsg",
    "         is only a wrapper script; the actual binary is at",
    "         /opt/homebrew/Cellar/imsg/<version>/libexec/imsg",
    "      -> Then run: textforme stop  &&  textforme start",
)


def _print_daemon_fda_warning_if_needed(daemon_status: dict | None) -> None:
    """After a successful daemon connection, warn if the daemon process itself
    (as opposed to this interactive terminal) lacks Full Disk Access.

    The daemon runs headless under launchd as a separate FDA-responsible
    process from the terminal that ran onboarding, so the local
    `_check_chat_db_readable` check above can pass while the daemon still
    can't read Messages. This is additional, non-fatal: onboarding still
    completes either way.
    """
    if daemon_status is None:
        return
    if daemon_status.get("chat_db_readable") is False:
        print()
        for line in _FDA_WARNING_LINES:
            print(line)


def run_onboarding() -> bool:
    """Interactive first-run flow. Returns True on success, False if aborted."""
    try:
        config.ensure_dirs()

        if not _run_system_checks():
            print("Please resolve the issues above, then run `textforme` again.")
            return False

        api_key = _prompt_api_key()
        keychain.set_api_key(api_key)
        print("  Key stored in the macOS Keychain.")
        print()

        model_id = _pick_model(api_key)
        print()

        print("Setting up the local database and syncing contacts...")
        db = Database(config.DB_PATH)
        try:
            imsg_ok = asyncio.run(_sync_contacts(db))
            if imsg_ok:
                print("  Messages connectivity looks good.")
            else:
                print("  Could not verify Messages connectivity yet (you can retry after setup).")
            db.set_setting("selected_model_id", model_id)
            db.set_setting("onboarding_complete", "true")
        finally:
            db.close()
        print()

        print("Installing the background service...")
        try:
            launchagent.install()
            launchagent.start()
        except launchagent.LaunchAgentError as exc:
            print(f"  Could not start the background service: {exc}")
            print("  You can retry later with: textforme install")
        if _wait_for_socket():
            print("  Service is running.")
        else:
            print("  Service did not report ready within 10s; check `textforme status` afterward.")

        print("Running a dry connectivity test (no messages will be sent)...")
        dry_ok, daemon_status = asyncio.run(_dry_test())
        if dry_ok:
            print("  Dry test passed.")
        else:
            print("  Dry test could not confirm connectivity; check Full Disk Access and imsg setup.")
        _print_daemon_fda_warning_if_needed(daemon_status)

        print()
        print("Setup complete. Launching TextForMe...")
        return True
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        return False
