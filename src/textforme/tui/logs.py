"""Log panel: tails the daemon's log file under the settings panel.

The daemon never logs message bodies, reply text, or the API key (only ids,
statuses, and error codes), so surfacing its log in the TUI leaks nothing.
The file is read directly (not via the daemon socket) so recent activity —
including crash output — stays visible even when the service is down.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from textual.widgets import Log

from .. import config

MAX_LINES = 200
_REFRESH_SECONDS = 2.0


def tail_lines(path: Path, max_lines: int = MAX_LINES) -> list[str]:
    """Last max_lines of a text file, newline-stripped; [] if unreadable."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in deque(fh, maxlen=max_lines)]
    except OSError:
        return []


def collect_log_lines(log_dir: Path | None = None, max_lines: int = MAX_LINES) -> list[str]:
    """Tail of the daemon's main log (config.LOG_DIR/daemon.log)."""
    directory = log_dir if log_dir is not None else config.LOG_DIR
    return tail_lines(directory / "daemon.log", max_lines)


class LogPanel(Log):
    """Read-only, auto-refreshing tail of the daemon log."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Logs"
        self._last_snapshot: tuple[str, ...] = ()

    def on_mount(self) -> None:
        self.refresh_logs()
        self.set_interval(_REFRESH_SECONDS, self.refresh_logs)

    def refresh_logs(self) -> None:
        lines = collect_log_lines()
        snapshot = tuple(lines)
        if snapshot == self._last_snapshot:
            return
        was_at_end = self.is_vertical_scroll_end
        self._last_snapshot = snapshot
        self.clear()
        if lines:
            self.write_lines(lines)
        else:
            self.write_line("(no log entries yet — logs live in ~/Library/Logs/TextForMe)")
        if was_at_end:
            self.scroll_end(animate=False)
