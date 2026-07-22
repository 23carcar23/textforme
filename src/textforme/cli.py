"""`textforme` entry point. Owner: Agent 3.

Usage:
  textforme            # onboarding on first run, else the desktop UI
  textforme tui        # the terminal UI
  textforme --dev      # desktop UI pointed at the Vite dev server
  textforme install    # (re)install + start the LaunchAgent
  textforme uninstall  # stop + remove LaunchAgent, keep data
  textforme start      # start the daemon (installs first if needed)
  textforme stop       # stop the daemon, keep it installed
  textforme status     # print daemon/launchagent status
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from . import launchagent
from .onboarding import needs_onboarding, run_onboarding
from .tui.app import DaemonClient, run_app

_USAGE = """Usage:
  textforme            # onboarding on first run, else the desktop UI
  textforme tui        # the terminal UI
  textforme --dev      # desktop UI pointed at the Vite dev server
  textforme install    # (re)install + start the LaunchAgent
  textforme uninstall  # stop + remove LaunchAgent, keep data
  textforme start      # start the daemon (installs first if needed)
  textforme stop       # stop the daemon, keep it installed
  textforme status     # print daemon/launchagent status
"""

_STATUS_FIELDS = (
    "running",
    "imsg_ok",
    "global_ai_enabled",
    "paused",
    "model_id",
    "replies_last_hour",
    "last_error",
)


def _cmd_install() -> int:
    launchagent.install()
    launchagent.start()
    print("TextForMe service installed and started.")
    return 0


def _cmd_uninstall() -> int:
    launchagent.uninstall()
    print("TextForMe service stopped and uninstalled (your data was kept).")
    return 0


def _cmd_start() -> int:
    launchagent.start()
    print("TextForMe service started.")
    return 0


def _cmd_stop() -> int:
    launchagent.stop()
    print("TextForMe service stopped (still installed; it will return at next login).")
    return 0


async def _fetch_daemon_status() -> dict[str, Any] | None:
    client = DaemonClient()
    try:
        if not await client.connect():
            return None
        return await client.request("status")
    except Exception:  # noqa: BLE001 - best-effort status probe
        return None
    finally:
        await client.close()


def _cmd_status() -> int:
    installed = launchagent.is_installed()
    running = launchagent.is_running()
    print(f"LaunchAgent installed: {'yes' if installed else 'no'}")
    print(f"LaunchAgent running:   {'yes' if running else 'no'}")

    status = asyncio.run(_fetch_daemon_status())
    if status is None:
        print("Daemon socket:         unreachable")
    else:
        print("Daemon socket:         reachable")
        for key in _STATUS_FIELDS:
            if key in status:
                print(f"  {key}: {status[key]}")
    return 0


def _cmd_default(dev: bool = False) -> int:
    if needs_onboarding():
        if not run_onboarding():
            return 1
    from .webui.window import run_webui  # deferred: imports pywebview/Cocoa

    return run_webui(dev=dev)


def _cmd_tui() -> int:
    if needs_onboarding():
        if not run_onboarding():
            return 1
    run_app()
    return 0


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""

    if command in ("", "run", "--dev"):
        code = _cmd_default(dev=command == "--dev")
    elif command == "tui":
        code = _cmd_tui()
    elif command == "install":
        code = _cmd_install()
    elif command == "uninstall":
        code = _cmd_uninstall()
    elif command == "start":
        code = _cmd_start()
    elif command == "stop":
        code = _cmd_stop()
    elif command == "status":
        code = _cmd_status()
    elif command in ("-h", "--help", "help"):
        print(_USAGE)
        code = 0
    else:
        print(f"Unknown command: {command}\n")
        print(_USAGE)
        code = 2

    sys.exit(code)
