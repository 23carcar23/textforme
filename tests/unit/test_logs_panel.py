"""Tests for the TUI log panel helpers and app mounting with the panel."""

from __future__ import annotations

from textforme import config
from textforme.tui.app import TextForMeApp
from textforme.tui.logs import LogPanel, collect_log_lines, tail_lines


def test_tail_lines_missing_file_returns_empty(tmp_path):
    assert tail_lines(tmp_path / "nope.log") == []


def test_tail_lines_returns_last_n_stripped(tmp_path):
    path = tmp_path / "daemon.log"
    path.write_text("".join(f"line {i}\n" for i in range(300)))
    lines = tail_lines(path, max_lines=200)
    assert len(lines) == 200
    assert lines[0] == "line 100"
    assert lines[-1] == "line 299"


def test_collect_log_lines_reads_daemon_log(tmp_path):
    (tmp_path / "daemon.log").write_text("hello\nworld\n")
    assert collect_log_lines(log_dir=tmp_path) == ["hello", "world"]


def test_collect_log_lines_empty_dir(tmp_path):
    assert collect_log_lines(log_dir=tmp_path) == []


async def test_app_mounts_log_panel_under_settings(tmp_path, monkeypatch):
    """The app mounts with the LogPanel present and populated, even with the
    daemon unreachable and no log file on disk."""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    (tmp_path / "daemon.log").write_text("2026-07-20 INFO textformed started\n")

    app = TextForMeApp(poll_interval=60.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(LogPanel)
        assert "textformed started" in "\n".join(panel.lines)
