"""Settings panel widget. Owner: Agent 3.

Rows: AI Service on/off, Anthropic Model, API Key status, Context Limit.

Per ARCHITECTURE.md §1 the TUI never talks to Anthropic or the Keychain
value directly (only presence, during onboarding). So for v1 the API Key
and Anthropic Model rows are read-only in the running app: replacing the
key or re-picking the model happens by re-running onboarding. All other
rows edit via daemon `settings.set`, sent immediately by the app in
response to the ``SettingsPanel.Changed`` message this widget posts.
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.containers import Grid
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, OptionList
from textual.widgets.option_list import Option

# (settings key, display label). Keys prefixed with "__" are synthetic
# (not a real settings.py key, handled specially below).
SETTINGS_ROWS: list[tuple[str, str]] = [
    ("global_ai_enabled", "AI Service"),
    ("selected_model_id", "Anthropic Model"),
    ("__api_key__", "API Key"),
    ("context_message_limit", "Context Limit"),
]

# Sensible cycle values (as strings, matching config.Settings typing) for
# the "Enter cycles a value" rows.
_CYCLES: dict[str, list[str]] = {
    "context_message_limit": ["5", "10", "25", "50", "unlimited"],
}

_API_KEY_HINT = "Replace the API key by re-running onboarding."


def _next_in_cycle(key: str, current: str) -> str:
    cycle = _CYCLES[key]
    try:
        idx = cycle.index(current)
    except ValueError:
        idx = -1
    return cycle[(idx + 1) % len(cycle)]


class ModelPickerModal(ModalScreen[str | None]):
    """Cycle through the Claude models available to the configured API key.

    Dismisses with the chosen model_id, or None if cancelled with Escape.
    """

    DEFAULT_CSS = """
    ModelPickerModal {
        align: center middle;
    }
    ModelPickerModal > Grid {
        grid-size: 1;
        grid-rows: auto 1fr auto;
        width: 70;
        max-height: 80%;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    ModelPickerModal OptionList {
        height: auto;
        max-height: 16;
    }
    """

    def __init__(self, models: list[dict[str, str]], current_id: str) -> None:
        super().__init__()
        self._models = models
        self._current_id = current_id
        self._chosen: str | None = None
        self._names = {m["model_id"]: m["display_name"] for m in models}

    def compose(self):
        with Grid():
            yield Label("Choose the Claude model (↑/↓ move, Enter select, Esc close):")
            yield OptionList(
                *[
                    Option(f"{m['display_name']}  ({m['model_id']})", id=m["model_id"])
                    for m in self._models
                ]
            )
            yield Label(self._status_text(), id="picker-status", classes="hint")

    def _status_text(self) -> str:
        if self._chosen is None:
            current = self._names.get(self._current_id) or self._current_id or "(unset)"
            return f"Current: {current} — select a model, then close (Esc) to apply."
        return f"Selected: {self._names.get(self._chosen, self._chosen)} — close (Esc) to apply."

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.focus()
        for index, model in enumerate(self._models):
            if model["model_id"] == self._current_id:
                option_list.highlighted = index
                break

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Selection does NOT close the window; the choice is applied when the
        # user closes it (Esc), so they can browse without committing.
        event.stop()
        self._chosen = event.option.id
        self.query_one("#picker-status", Label).update(self._status_text())

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(self._chosen)


class SettingsPanel(DataTable):
    """Two-column settings table: label | value."""

    class Changed(Message):
        """Posted when the user has picked a new value for a settings key."""

        def __init__(self, key: str, value: str) -> None:
            self.key = key
            self.value = value
            super().__init__()

    class ModelPickRequested(Message):
        """Posted when the user activates the Anthropic Model row."""

    class ModelCycleRequested(Message):
        """Posted when the user presses ←/→ on the Anthropic Model row."""

        def __init__(self, delta: int) -> None:
            self.delta = delta
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._settings: dict[str, str] = {}
        self._api_key_configured = False
        self._model_names: dict[str, str] = {}
        self._columns_built = False
        self._rows_built = False

    def on_mount(self) -> None:
        self._ensure_built()

    def _ensure_built(self) -> None:
        if not self._columns_built:
            self.add_column("Setting", key="label", width=16)
            self.add_column("Value", key="value")
            self._columns_built = True
        if not self._rows_built:
            for key, label in SETTINGS_ROWS:
                self.add_row(label, "", key=key)
            self._rows_built = True

    def load(
        self,
        settings: dict[str, str],
        api_key_configured: bool,
        model_names: dict[str, str] | None = None,
    ) -> None:
        """model_names maps model_id -> display_name; None keeps the last map
        (so a transient models.list failure doesn't blank the model name)."""
        self._ensure_built()
        self._settings = dict(settings)
        self._api_key_configured = api_key_configured
        if model_names is not None:
            self._model_names = dict(model_names)
        for key, _label in SETTINGS_ROWS:
            try:
                # update_width=True: the column starts sized to the empty
                # placeholder rows, so without it every value renders
                # truncated to the header's width ("Value" -> 5 chars).
                self.update_cell(key, "value", self._display_value(key), update_width=True)
            except Exception:
                pass

    def current_value(self, key: str) -> str:
        """Raw current value for a real settings key ('' if unknown)."""
        return self._settings.get(key, "")

    def _display_value(self, key: str) -> str:
        if key == "__api_key__":
            return "Configured" if self._api_key_configured else "Not configured"
        if key == "global_ai_enabled":
            return "ON" if self._settings.get("global_ai_enabled") == "true" else "OFF"
        if key == "selected_model_id":
            model_id = self._settings.get("selected_model_id", "")
            return self._model_names.get(model_id) or model_id or "(unset)"
        return self._settings.get(key, "")

    def _cursor_row_key(self) -> str | None:
        if not self.row_count or self.cursor_row is None:
            return None
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return row_key.value

    def on_key(self, event: events.Key) -> None:
        if event.key in ("left", "right") and self._cursor_row_key() == "selected_model_id":
            event.stop()
            event.prevent_default()
            self.post_message(self.ModelCycleRequested(1 if event.key == "right" else -1))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        key = event.row_key.value
        if key is None:
            return
        if key == "__api_key__":
            self.notify(_API_KEY_HINT, severity="information")
            return
        if key == "selected_model_id":
            # The app fetches the live model list from the daemon and opens
            # the picker; the TUI itself never talks to Anthropic.
            self.post_message(self.ModelPickRequested())
            return
        if key == "global_ai_enabled":
            new_value = "false" if self._settings.get("global_ai_enabled") == "true" else "true"
            self.post_message(self.Changed(key, new_value))
            return
        if key in _CYCLES:
            current = self._settings.get(key, _CYCLES[key][0])
            new_value = _next_in_cycle(key, current)
            self.post_message(self.Changed(key, new_value))
            return
