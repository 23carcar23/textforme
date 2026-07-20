"""Settings panel widget. Owner: Agent 3.

Rows: AI Service on/off, Anthropic Model, API Key status, Reply Length,
Response Delay, Context Limit, Quiet Hours, Global Rate Limit.

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
from textual.widgets import DataTable, Input, Label, OptionList
from textual.widgets.option_list import Option

# (settings key, display label). Keys prefixed with "__" are synthetic
# (not a real settings.py key, handled specially below).
SETTINGS_ROWS: list[tuple[str, str]] = [
    ("global_ai_enabled", "AI Service"),
    ("selected_model_id", "Anthropic Model"),
    ("__api_key__", "API Key"),
    ("maximum_reply_length", "Reply Length"),
    ("response_delay_seconds", "Response Delay"),
    ("context_message_limit", "Context Limit"),
    ("__quiet_hours__", "Quiet Hours"),
    ("global_rate_limit_per_hour", "Rate Limit"),
]

# Sensible cycle values (as strings, matching config.Settings typing) for
# the "Enter cycles a value" rows.
_CYCLES: dict[str, list[str]] = {
    "response_delay_seconds": ["0", "3", "10", "30"],
    "maximum_reply_length": ["150", "300", "600"],
    "context_message_limit": ["5", "10", "25"],
    "global_rate_limit_per_hour": ["10", "20", "60"],
}

_API_KEY_HINT = "Replace the API key by re-running onboarding."


def _next_in_cycle(key: str, current: str) -> str:
    cycle = _CYCLES[key]
    try:
        idx = cycle.index(current)
    except ValueError:
        idx = -1
    return cycle[(idx + 1) % len(cycle)]


class QuietHoursModal(ModalScreen[str | None]):
    """Prompt for 'HH:MM-HH:MM', or empty to disable quiet hours.

    Dismisses with the raw string typed (possibly empty) or None if
    cancelled with Escape.
    """

    DEFAULT_CSS = """
    QuietHoursModal {
        align: center middle;
    }
    QuietHoursModal > Grid {
        grid-size: 1;
        grid-rows: auto auto auto;
        width: 50;
        height: 9;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    """

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self):
        with Grid():
            yield Label("Quiet hours as HH:MM-HH:MM (empty disables):")
            yield Input(value=self._current, placeholder="22:00-07:00", id="quiet-hours-input")
            yield Label("Enter to save, Escape to cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


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

    def compose(self):
        with Grid():
            yield Label("Choose the Claude model (↑/↓ cycle, Enter select, Esc cancel):")
            yield OptionList(
                *[
                    Option(f"{m['display_name']}  ({m['model_id']})", id=m["model_id"])
                    for m in self._models
                ]
            )
            yield Label("Takes effect immediately for new replies.", classes="hint")

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.focus()
        for index, model in enumerate(self._models):
            if model["model_id"] == self._current_id:
                option_list.highlighted = index
                break

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._settings: dict[str, str] = {}
        self._api_key_configured = False
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

    def load(self, settings: dict[str, str], api_key_configured: bool) -> None:
        self._ensure_built()
        self._settings = dict(settings)
        self._api_key_configured = api_key_configured
        for key, _label in SETTINGS_ROWS:
            try:
                self.update_cell(key, "value", self._display_value(key))
            except Exception:
                pass

    def current_value(self, key: str) -> str:
        """Raw current value for a real settings key ('' if unknown)."""
        return self._settings.get(key, "")

    def _display_value(self, key: str) -> str:
        if key == "__api_key__":
            return "Configured" if self._api_key_configured else "Not configured"
        if key == "__quiet_hours__":
            start = self._settings.get("quiet_hours_start", "")
            end = self._settings.get("quiet_hours_end", "")
            return f"{start}-{end}" if start and end else "Off"
        if key == "global_ai_enabled":
            return "ON" if self._settings.get("global_ai_enabled") == "true" else "OFF"
        if key == "selected_model_id":
            return self._settings.get("selected_model_id") or "(unset)"
        return self._settings.get(key, "")

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
        if key == "__quiet_hours__":
            self._prompt_quiet_hours()
            return
        if key in _CYCLES:
            current = self._settings.get(key, _CYCLES[key][0])
            new_value = _next_in_cycle(key, current)
            self.post_message(self.Changed(key, new_value))
            return

    def _prompt_quiet_hours(self) -> None:
        current_display = self._display_value("__quiet_hours__")
        current = "" if current_display == "Off" else current_display

        def _apply(result: str | None) -> None:
            if result is None:
                return
            if result == "":
                self.post_message(self.Changed("quiet_hours_start", ""))
                self.post_message(self.Changed("quiet_hours_end", ""))
                return
            if "-" not in result:
                self.notify("Use the form HH:MM-HH:MM", severity="error")
                return
            start, _, end = result.partition("-")
            self.post_message(self.Changed("quiet_hours_start", start.strip()))
            self.post_message(self.Changed("quiet_hours_end", end.strip()))

        self.app.push_screen(QuietHoursModal(current), _apply)
