"""Tests for the TUI model picker modal."""

from __future__ import annotations

from textual.app import App

from textforme.tui.settings import ModelPickerModal

MODELS = [
    {"model_id": "claude-a", "display_name": "Claude A"},
    {"model_id": "claude-b", "display_name": "Claude B"},
    {"model_id": "claude-c", "display_name": "Claude C"},
]


class Host(App[None]):
    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current
        self.results: list[str | None] = []

    def on_mount(self) -> None:
        self.push_screen(ModelPickerModal(MODELS, self.current), self.results.append)


async def test_enter_selects_highlighted_current_model():
    app = Host(current="claude-b")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.results == ["claude-b"]


async def test_arrow_cycles_then_enter_selects_next_model():
    app = Host(current="claude-b")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
    assert app.results == ["claude-c"]


async def test_escape_cancels_with_none():
    app = Host(current="claude-a")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.results == [None]
