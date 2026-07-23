"""Tests for cli.py subcommand dispatch. launchagent, onboarding, and the
TUI's DaemonClient/run_app are all mocked via monkeypatch -- no real
LaunchAgent, socket, or Textual app is touched."""

from __future__ import annotations

import pytest

from textforme import cli


class FakeDaemonClient:
    """Stand-in for tui.app.DaemonClient used by `textforme status`."""

    def __init__(self, socket_path=None, timeout: float = 5.0) -> None:
        pass

    async def connect(self) -> bool:
        return False

    async def request(self, method: str, params: dict | None = None) -> dict:
        return {}

    async def close(self) -> None:
        return None


class ConnectedFakeDaemonClient(FakeDaemonClient):
    async def connect(self) -> bool:
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        assert method == "status"
        return {
            "running": True,
            "imsg_ok": True,
            "chat_db_readable": True,
            "global_ai_enabled": True,
            "paused": False,
            "model_id": "claude-x",
            "replies_last_hour": 2,
            "last_error": None,
        }


# -- default command (onboarding + desktop UI) ---------------------------


def _patch_webui(monkeypatch, sink):
    """Stub the deferred `from .webui.window import run_webui` import so the
    default command can be tested without pywebview/Cocoa."""
    import sys as _sys
    import types

    module = types.ModuleType("textforme.webui.window")

    def _run_webui(dev: bool = False) -> int:
        sink.append("dev" if dev else "ran")
        return 0

    module.run_webui = _run_webui
    monkeypatch.setitem(_sys.modules, "textforme.webui.window", module)


def test_default_runs_onboarding_when_needed_then_launches_ui(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme"])
    monkeypatch.setattr(cli, "needs_onboarding", lambda: True)

    onboarding_calls = []
    monkeypatch.setattr(cli, "run_onboarding", lambda: (onboarding_calls.append("ran"), True)[1])

    ui_calls = []
    _patch_webui(monkeypatch, ui_calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert onboarding_calls == ["ran"]
    assert ui_calls == ["ran"]


def test_default_skips_onboarding_when_not_needed(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme"])
    monkeypatch.setattr(cli, "needs_onboarding", lambda: False)

    onboarding_calls = []
    monkeypatch.setattr(cli, "run_onboarding", lambda: onboarding_calls.append("ran"))

    ui_calls = []
    _patch_webui(monkeypatch, ui_calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert onboarding_calls == []
    assert ui_calls == ["ran"]


def test_dev_flag_launches_ui_in_dev_mode(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "--dev"])
    monkeypatch.setattr(cli, "needs_onboarding", lambda: False)

    ui_calls = []
    _patch_webui(monkeypatch, ui_calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert ui_calls == ["dev"]


def test_default_exits_nonzero_and_skips_ui_when_onboarding_fails(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme"])
    monkeypatch.setattr(cli, "needs_onboarding", lambda: True)
    monkeypatch.setattr(cli, "run_onboarding", lambda: False)

    ui_calls = []
    _patch_webui(monkeypatch, ui_calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code != 0
    assert ui_calls == []


def test_tui_subcommand_launches_terminal_app(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "tui"])
    monkeypatch.setattr(cli, "needs_onboarding", lambda: False)

    app_calls = []
    monkeypatch.setattr(cli, "run_app", lambda: app_calls.append("ran"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert app_calls == ["ran"]


# -- install / uninstall ---------------------------------------------------


def test_install_subcommand_calls_launchagent_install_and_start(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "install"])

    calls = []
    monkeypatch.setattr(cli.launchagent, "install", lambda: calls.append("install"))
    monkeypatch.setattr(cli.launchagent, "start", lambda: calls.append("start"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls == ["install", "start"]


def test_install_subcommand_prints_error_and_exits_1_on_launchagent_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "install"])

    def _raise_install():
        raise cli.launchagent.LaunchAgentError("launchctl bootstrap failed: Bootstrap failed: 5: Input/output error")

    monkeypatch.setattr(cli.launchagent, "install", _raise_install)
    monkeypatch.setattr(cli.launchagent, "start", lambda: None)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr()
    assert "Bootstrap failed" in output.err
    assert "daemon.err.log" in output.err
    assert "installed and started" not in output.out


def test_uninstall_subcommand_calls_launchagent_uninstall(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "uninstall"])

    calls = []
    monkeypatch.setattr(cli.launchagent, "uninstall", lambda: calls.append("uninstall"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls == ["uninstall"]


# -- status ------------------------------------------------------------


def test_status_subcommand_reports_unreachable_daemon(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "status"])
    monkeypatch.setattr(cli.launchagent, "is_installed", lambda: True)
    monkeypatch.setattr(cli.launchagent, "is_running", lambda: False)
    monkeypatch.setattr(cli, "DaemonClient", FakeDaemonClient)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "installed: yes" in output.lower()
    assert "running:   no" in output.lower() or "running: no" in output.lower()
    assert "unreachable" in output.lower()


def test_status_subcommand_reports_reachable_daemon_fields(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "status"])
    monkeypatch.setattr(cli.launchagent, "is_installed", lambda: True)
    monkeypatch.setattr(cli.launchagent, "is_running", lambda: True)
    monkeypatch.setattr(cli, "DaemonClient", ConnectedFakeDaemonClient)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "reachable" in output.lower()
    assert "claude-x" in output
    assert "chat_db_readable: True" in output


# -- unknown command ----------------------------------------------------


def test_unknown_subcommand_exits_nonzero_and_prints_usage(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "bogus"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code != 0
    output = capsys.readouterr().out
    assert "Usage" in output


def test_start_subcommand_calls_launchagent_start(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "start"])

    calls = []
    monkeypatch.setattr(cli.launchagent, "start", lambda: calls.append("start"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
    assert calls == ["start"]


def test_stop_subcommand_calls_launchagent_stop(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "stop"])

    calls = []
    monkeypatch.setattr(cli.launchagent, "stop", lambda: calls.append("stop"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
    assert calls == ["stop"]


def test_start_subcommand_prints_error_and_exits_1_on_launchagent_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "start"])

    def _raise_start():
        raise cli.launchagent.LaunchAgentError("launchctl kickstart failed: No such process")

    monkeypatch.setattr(cli.launchagent, "start", _raise_start)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr()
    assert "No such process" in output.err
    assert "service started" not in output.out


def test_stop_subcommand_prints_error_and_exits_1_on_launchagent_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "stop"])

    def _raise_stop():
        raise cli.launchagent.LaunchAgentError("launchctl bootout failed: Operation not permitted")

    monkeypatch.setattr(cli.launchagent, "stop", _raise_stop)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr()
    assert "Operation not permitted" in output.err
    assert "service stopped" not in output.out


def test_uninstall_subcommand_prints_error_and_exits_1_on_launchagent_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["textforme", "uninstall"])

    def _raise_uninstall():
        raise cli.launchagent.LaunchAgentError("launchctl bootout failed: Operation not permitted")

    monkeypatch.setattr(cli.launchagent, "uninstall", _raise_uninstall)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr()
    assert "Operation not permitted" in output.err
    assert "uninstalled" not in output.out
