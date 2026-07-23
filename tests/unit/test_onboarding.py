"""Tests for onboarding.py. All collaborators (keychain, launchagent,
AnthropicClient, Database, ImsgClient, DaemonClient) are mocked via
monkeypatch -- no real Keychain, network, imsg, or filesystem DB writes."""

from __future__ import annotations

import pytest

from textforme import config, keychain, onboarding
from textforme.anthropic.models import ModelInfo
from textforme.config import Settings


class FakeDatabase:
    """Stand-in for database.Database that records calls in memory."""

    def __init__(self, path=None) -> None:
        self.path = path
        self.settings: dict[str, str] = {}
        self.contacts: list = []
        self.closed = False

    def get_settings(self) -> Settings:
        return Settings.from_mapping(self.settings)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def upsert_contact(self, contact) -> None:
        self.contacts.append(contact)

    def close(self) -> None:
        self.closed = True


class FakeAnthropicClient:
    """Stand-in for anthropic.client.AnthropicClient."""

    def __init__(self, api_key: str, timeout_seconds: float = 30.0) -> None:
        self.api_key = api_key

    async def validate_key(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(model_id="claude-x", display_name="Claude X")]


class RejectingAnthropicClient(FakeAnthropicClient):
    """A key validator that rejects the first key it sees, then accepts."""

    calls = 0

    async def validate_key(self) -> bool:
        RejectingAnthropicClient.calls += 1
        return RejectingAnthropicClient.calls > 1


class FakeImsgClient:
    """Stand-in for messaging.client.ImsgClient."""

    def __init__(self, binary: str = "imsg") -> None:
        self.binary = binary

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def list_contacts(self, limit: int = 200) -> list:
        return []

    async def health_check(self) -> bool:
        return True


class FakeDaemonClientConnected:
    """Stand-in for tui.app.DaemonClient that always succeeds."""

    def __init__(self, socket_path=None, timeout: float = 5.0) -> None:
        pass

    async def connect(self) -> bool:
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        return {}

    async def close(self) -> None:
        return None


class FakeDaemonClientFdaBlocked:
    """Stand-in for tui.app.DaemonClient: connects, but the daemon reports it
    cannot read chat.db (headless process lacks Full Disk Access)."""

    def __init__(self, socket_path=None, timeout: float = 5.0) -> None:
        pass

    async def connect(self) -> bool:
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        return {"running": True, "imsg_ok": False, "chat_db_readable": False}

    async def close(self) -> None:
        return None


# -- needs_onboarding() -----------------------------------------------------


def test_needs_onboarding_true_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "does-not-exist.db")
    monkeypatch.setattr(keychain, "has_api_key", lambda: True)
    assert onboarding.needs_onboarding() is True


def test_needs_onboarding_true_when_no_api_key(tmp_path, monkeypatch):
    db_path = tmp_path / "textforme.db"
    db_path.touch()
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(keychain, "has_api_key", lambda: False)
    assert onboarding.needs_onboarding() is True


def test_needs_onboarding_true_when_onboarding_incomplete(tmp_path, monkeypatch):
    db_path = tmp_path / "textforme.db"
    db_path.touch()
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(keychain, "has_api_key", lambda: True)

    fake_db = FakeDatabase(db_path)
    fake_db.settings["onboarding_complete"] = "false"
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)

    assert onboarding.needs_onboarding() is True
    assert fake_db.closed is True


def test_needs_onboarding_false_when_everything_ready(tmp_path, monkeypatch):
    db_path = tmp_path / "textforme.db"
    db_path.touch()
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(keychain, "has_api_key", lambda: True)

    fake_db = FakeDatabase(db_path)
    fake_db.settings["onboarding_complete"] = "true"
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)

    assert onboarding.needs_onboarding() is False


def test_needs_onboarding_true_when_db_open_raises(tmp_path, monkeypatch):
    db_path = tmp_path / "textforme.db"
    db_path.touch()
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(keychain, "has_api_key", lambda: True)

    def _boom(path):
        raise RuntimeError("corrupt db")

    monkeypatch.setattr(onboarding, "Database", _boom)
    assert onboarding.needs_onboarding() is True


# -- run_onboarding() ---------------------------------------------------


def _patch_happy_system_checks(monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "_check_macos_version", lambda: True)
    monkeypatch.setattr(onboarding, "_check_imsg_on_path", lambda: True)
    monkeypatch.setattr(onboarding, "_check_chat_db_readable", lambda: True)


def test_run_onboarding_stores_key_in_keychain_and_never_in_db(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "textforme.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SOCKET_PATH", tmp_path / "daemon.sock")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    _patch_happy_system_checks(monkeypatch)

    fake_key = "sk-ant-super-secret-value-should-never-leak"
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda prompt="": fake_key)
    monkeypatch.setattr(onboarding, "AnthropicClient", FakeAnthropicClient)

    set_key_calls: list[str] = []
    monkeypatch.setattr(onboarding.keychain, "set_api_key", lambda key: set_key_calls.append(key))

    fake_db = FakeDatabase(db_path)
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)
    monkeypatch.setattr(onboarding, "ImsgClient", FakeImsgClient)
    monkeypatch.setattr(onboarding, "DaemonClient", FakeDaemonClientConnected)

    install_calls: list[str] = []
    monkeypatch.setattr(onboarding.launchagent, "install", lambda: install_calls.append("install"))
    monkeypatch.setattr(onboarding.launchagent, "start", lambda: install_calls.append("start"))
    monkeypatch.setattr(onboarding, "_wait_for_socket", lambda timeout=10.0: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    result = onboarding.run_onboarding()

    assert result is True
    # The key must be stored in the Keychain exactly once, and only there.
    assert set_key_calls == [fake_key]

    # The raw key must never end up in anything written to the DB.
    for value in fake_db.settings.values():
        assert fake_key not in value

    assert fake_db.settings["selected_model_id"] == "claude-x"
    assert fake_db.settings["onboarding_complete"] == "true"
    assert install_calls == ["install", "start"]

    # ...nor printed to the terminal.
    captured = capsys.readouterr()
    assert fake_key not in captured.out
    assert fake_key not in captured.err


def test_run_onboarding_survives_launchagent_error(tmp_path, monkeypatch, capsys):
    """If launchagent.install()/start() raise LaunchAgentError, onboarding
    prints the error and a retry hint but still completes successfully."""
    db_path = tmp_path / "textforme.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SOCKET_PATH", tmp_path / "daemon.sock")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    _patch_happy_system_checks(monkeypatch)

    fake_key = "sk-ant-super-secret-value-should-never-leak"
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda prompt="": fake_key)
    monkeypatch.setattr(onboarding, "AnthropicClient", FakeAnthropicClient)
    monkeypatch.setattr(onboarding.keychain, "set_api_key", lambda key: None)

    fake_db = FakeDatabase(db_path)
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)
    monkeypatch.setattr(onboarding, "ImsgClient", FakeImsgClient)
    monkeypatch.setattr(onboarding, "DaemonClient", FakeDaemonClientConnected)

    def _raise_install():
        raise onboarding.launchagent.LaunchAgentError(
            "launchctl bootstrap failed: Bootstrap failed: 5: Input/output error"
        )

    monkeypatch.setattr(onboarding.launchagent, "install", _raise_install)
    start_calls: list[str] = []
    monkeypatch.setattr(onboarding.launchagent, "start", lambda: start_calls.append("start"))
    monkeypatch.setattr(onboarding, "_wait_for_socket", lambda timeout=10.0: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    result = onboarding.run_onboarding()

    # Setup still completes -- the failure is degraded-mode, not fatal.
    assert result is True
    # start() must not be called since install() raised.
    assert start_calls == []

    output = capsys.readouterr().out
    assert "Could not start the background service" in output
    assert "Bootstrap failed" in output
    assert "textforme install" in output


def test_run_onboarding_reprompts_on_rejected_key(tmp_path, monkeypatch):
    db_path = tmp_path / "textforme.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SOCKET_PATH", tmp_path / "daemon.sock")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    _patch_happy_system_checks(monkeypatch)

    RejectingAnthropicClient.calls = 0
    keys_tried = iter(["bad-key", "good-key"])
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda prompt="": next(keys_tried))
    monkeypatch.setattr(onboarding, "AnthropicClient", RejectingAnthropicClient)

    set_key_calls: list[str] = []
    monkeypatch.setattr(onboarding.keychain, "set_api_key", lambda key: set_key_calls.append(key))

    fake_db = FakeDatabase(db_path)
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)
    monkeypatch.setattr(onboarding, "ImsgClient", FakeImsgClient)
    monkeypatch.setattr(onboarding, "DaemonClient", FakeDaemonClientConnected)
    monkeypatch.setattr(onboarding.launchagent, "install", lambda: None)
    monkeypatch.setattr(onboarding.launchagent, "start", lambda: None)
    monkeypatch.setattr(onboarding, "_wait_for_socket", lambda timeout=10.0: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    result = onboarding.run_onboarding()

    assert result is True
    # Only the accepted key was ever stored.
    assert set_key_calls == ["good-key"]


def test_run_onboarding_aborts_without_touching_keychain_on_system_check_failure(monkeypatch):
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(onboarding, "_check_macos_version", lambda: False)
    monkeypatch.setattr(onboarding, "_check_imsg_on_path", lambda: True)
    monkeypatch.setattr(onboarding, "_check_chat_db_readable", lambda: True)

    set_key_calls: list[str] = []
    monkeypatch.setattr(onboarding.keychain, "set_api_key", lambda key: set_key_calls.append(key))

    result = onboarding.run_onboarding()

    assert result is False
    assert set_key_calls == []


def test_run_onboarding_warns_when_daemon_lacks_full_disk_access(tmp_path, monkeypatch, capsys):
    """The local (terminal-side) check can pass while the headless daemon
    process still can't read chat.db -- onboarding should surface that
    specific warning (from the daemon's own status response) but still
    complete successfully."""
    db_path = tmp_path / "textforme.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SOCKET_PATH", tmp_path / "daemon.sock")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    _patch_happy_system_checks(monkeypatch)

    fake_key = "sk-ant-super-secret-value-should-never-leak"
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda prompt="": fake_key)
    monkeypatch.setattr(onboarding, "AnthropicClient", FakeAnthropicClient)
    monkeypatch.setattr(onboarding.keychain, "set_api_key", lambda key: None)

    fake_db = FakeDatabase(db_path)
    monkeypatch.setattr(onboarding, "Database", lambda path: fake_db)
    monkeypatch.setattr(onboarding, "ImsgClient", FakeImsgClient)
    monkeypatch.setattr(onboarding, "DaemonClient", FakeDaemonClientFdaBlocked)
    monkeypatch.setattr(onboarding.launchagent, "install", lambda: None)
    monkeypatch.setattr(onboarding.launchagent, "start", lambda: None)
    monkeypatch.setattr(onboarding, "_wait_for_socket", lambda timeout=10.0: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    result = onboarding.run_onboarding()

    assert result is True
    output = capsys.readouterr().out
    assert "cannot read the Messages database" in output
    assert "Full Disk Access" in output
    assert "/opt/homebrew/bin/imsg" in output
    assert "libexec/imsg" in output
    assert "textforme stop" in output
    assert "textforme start" in output


def test_run_onboarding_handles_keyboard_interrupt_cleanly(monkeypatch, capsys):
    def _raise(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(config, "ensure_dirs", _raise)

    result = onboarding.run_onboarding()

    assert result is False
    captured = capsys.readouterr()
    assert "cancelled" in captured.out.lower()
