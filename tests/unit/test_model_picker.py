"""Tests for the model picker modal, model-name display, and ←/→ cycling."""

from __future__ import annotations

from textual.app import App

from textforme import config
from textforme.tui import app as app_module
from textforme.tui.app import TextForMeApp
from textforme.tui.settings import ModelPickerModal, SettingsPanel

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


class FakeDaemonClient:
    """Stand-in DaemonClient speaking the §5 protocol surface the app uses."""

    def __init__(self, selected: str = "claude-b") -> None:
        self.selected = selected
        self.set_calls: list[dict] = []

    async def connect(self) -> bool:
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        if method == "status":
            return {"running": True, "imsg_ok": True, "global_ai_enabled": True,
                    "paused": False, "model_id": self.selected,
                    "replies_last_hour": 0, "last_error": None}
        if method == "contacts.list":
            return {"contacts": []}
        if method == "settings.get":
            settings = dict(config.DEFAULT_SETTINGS)
            settings["selected_model_id"] = self.selected
            return {"settings": settings}
        if method == "settings.set":
            assert params is not None
            self.set_calls.append(dict(params))
            if params["key"] == "selected_model_id":
                self.selected = params["value"]
            return {}
        if method == "models.list":
            return {"models": MODELS}
        return {}

    async def close(self) -> None:
        return None


# -- modal: select-then-close-to-apply -------------------------------------------


async def test_enter_selects_but_modal_stays_open_until_escape_applies():
    app = Host(current="claude-b")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.results == []  # selection made, window still open
        await pilot.press("escape")
        await pilot.pause()
    assert app.results == ["claude-c"]


async def test_escape_without_selection_returns_none():
    app = Host(current="claude-a")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "escape")  # browsing without Enter = no choice
        await pilot.pause()
    assert app.results == [None]


# -- settings row: display name + arrow cycling -----------------------------------


def _make_app(monkeypatch, selected: str = "claude-b"):
    monkeypatch.setattr(app_module, "has_api_key", lambda: True)
    client = FakeDaemonClient(selected=selected)
    return TextForMeApp(client=client, poll_interval=60.0), client


async def test_model_row_shows_display_name_not_id(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    app, _client = _make_app(monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(SettingsPanel)
        assert str(panel.get_cell("selected_model_id", "value")) == "Claude B"
        # The value column must be wide enough to actually RENDER the values —
        # it starts sized to the empty placeholder rows and the 5-char "Value"
        # header, which truncated everything to "Claud" before the
        # update_width=True fix.
        value_column = panel.ordered_columns[1]
        assert value_column.get_render_width(panel) >= len("Claude B")


async def test_right_and_left_arrows_cycle_models_on_model_row(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    app, client = _make_app(monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(SettingsPanel)
        panel.focus()
        panel.move_cursor(row=1)  # the Anthropic Model row
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause()
        assert client.set_calls[-1] == {"key": "selected_model_id", "value": "claude-c"}

        await pilot.press("left")
        await pilot.pause()
        assert client.set_calls[-1] == {"key": "selected_model_id", "value": "claude-b"}

        # Row re-renders with the display name of the new selection.
        assert str(panel.get_cell("selected_model_id", "value")) == "Claude B"


async def test_arrows_on_other_rows_do_not_change_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    app, client = _make_app(monkeypatch)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        panel = app.query_one(SettingsPanel)
        panel.focus()
        panel.move_cursor(row=0)  # AI Service row
        await pilot.press("right", "left")
        await pilot.pause()
    assert client.set_calls == []
