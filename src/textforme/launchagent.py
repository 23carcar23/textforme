"""LaunchAgent install/uninstall for textformed. Owner: Agent 6.

Renders resources/launchagent.plist with the absolute path of the current
interpreter's `textformed` console script, writes it to
config.LAUNCH_AGENT_PATH, and manages it via `launchctl`
(bootstrap/bootout gui/$UID, kickstart). Logs go to config.LOG_DIR.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config


class LaunchAgentError(RuntimeError):
    """A launchctl subcommand failed and the daemon's actual load state
    doesn't excuse the failure (see install()/start()/stop() docstrings)."""


def _launchctl_error(subcommand: str, result: subprocess.CompletedProcess) -> LaunchAgentError:
    stderr = (result.stderr or "").strip()
    detail = stderr if stderr else f"exit code {result.returncode}"
    return LaunchAgentError(f"launchctl {subcommand} failed: {detail}")


# Embedded plist template
PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.textforme.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{TEXTFORMED_PATH}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/daemon.out.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/daemon.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


def _get_textformed_path() -> str:
    """Get the absolute path to the textformed console script."""
    # Try to find textformed via shutil.which
    path = shutil.which("textformed")
    if path:
        return path
    # Fallback: construct path from sys.executable
    return str(Path(sys.executable).parent / "textformed")


def install() -> None:
    """Write the plist and (re)load the agent. Idempotent.

    Raises:
        LaunchAgentError: if `launchctl bootstrap` fails and the job isn't
            actually loaded afterward. `bootstrap` can report a failure
            (e.g. "Bootstrap failed: 5: Input/output error", or exit 37
            "already bootstrapped") even though the job ends up loaded —
            since we bootout first this should be rare, but we treat it as
            success rather than raise a false alarm.
    """
    config.ensure_dirs()

    # Render the plist template
    textformed_path = _get_textformed_path()
    log_dir = str(config.LOG_DIR)
    plist_content = PLIST_TEMPLATE.format(
        TEXTFORMED_PATH=textformed_path,
        LOG_DIR=log_dir,
    )

    # Write the plist file
    config.LAUNCH_AGENT_PATH.write_text(plist_content)

    # (Re)load via launchctl
    uid = os.getuid()

    # First, try to unload the agent. This is best-effort: booting out a
    # job that isn't currently loaded legitimately fails, so its result is
    # ignored.
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(config.LAUNCH_AGENT_PATH)],
        capture_output=True,
        text=True,
    )

    # Then load it.
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(config.LAUNCH_AGENT_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not is_running():
        raise _launchctl_error("bootstrap", result)


def uninstall() -> None:
    """Unload and remove the plist. Idempotent."""
    stop()
    config.LAUNCH_AGENT_PATH.unlink(missing_ok=True)


def is_installed() -> bool:
    return config.LAUNCH_AGENT_PATH.exists()


def is_running() -> bool:
    """True if launchctl reports the service as running (has a PID)."""
    uid = os.getuid()
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{config.LAUNCH_AGENT_LABEL}"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "pid = " in result.stdout
    except Exception:
        return False


def start() -> None:
    """Ensure the agent is loaded and running.

    `stop()` boots the job out of launchd entirely, so starting must
    re-bootstrap (install() is idempotent and does bootout+bootstrap),
    not merely kickstart — kickstarting an unloaded job is a no-op.

    Raises:
        LaunchAgentError: propagated from install() if bootstrap fails, or
            raised here if `launchctl kickstart` fails and the job is not
            actually running afterward.
    """
    install()

    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "kickstart", f"gui/{uid}/{config.LAUNCH_AGENT_LABEL}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not is_running():
        raise _launchctl_error("kickstart", result)


def stop() -> None:
    """Stop the running daemon without uninstalling.

    Raises:
        LaunchAgentError: if `launchctl bootout` fails and the job is
            still running afterward. Booting out a job that isn't loaded
            legitimately fails and is ignored.
    """
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{config.LAUNCH_AGENT_LABEL}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and is_running():
        raise _launchctl_error("bootout", result)
