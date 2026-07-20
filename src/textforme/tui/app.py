"""Minimal Textual app. Owner: Agent 3.

Layout per PRD §5: header "TextForMe  Service: Running/Stopped", contacts table
(AI | Contact | Number) with Space toggling, settings panel, contact-note box
(per-contact prompt guidance), footer keys (↑/↓ Move, Space Toggle,
Tab Settings, S Save, Shift+L Logs, Q Quit). Daemon logs open in a Shift+L
popup instead of a persistent panel.

MUST NOT display message previews, history, composition, or AI-reply previews.

Includes DaemonClient: a small asyncio JSON-lines client for the Unix socket
(ARCHITECTURE §5) with connect/request/close and a is_connected property.
If the daemon is unreachable: show "Service: Stopped", disable toggling, and
poll for reconnection every few seconds.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static

from .. import config
from ..keychain import has_api_key
from .contacts import ContactsTable
from .logs import LogModal
from .settings import ModelPickerModal, SettingsPanel

_CONNECTION_ERRORS = {"NOT_CONNECTED", "CONNECTION_ERROR", "CONNECTION_CLOSED"}

_FOOTER_TEXT = (
    "↑/↓ Move    Space Toggle    Tab Settings    S Save    Shift+L Logs    Q Quit"
)


class DaemonClient:
    """Asyncio JSON-lines client for the daemon's Unix socket (ARCHITECTURE §5).

    One request in flight at a time; each request gets an auto-incrementing
    id and a 5s timeout. Connection failures and error responses both raise
    ``RuntimeError`` (message is the protocol error code, or a local code
    such as ``NOT_CONNECTED``/``CONNECTION_ERROR``/``CONNECTION_CLOSED``).
    """

    def __init__(self, socket_path: Path | str | None = None, timeout: float = 5.0) -> None:
        self._socket_path = str(socket_path) if socket_path is not None else str(config.SOCKET_PATH)
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> bool:
        if self.is_connected:
            return True
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path), timeout=self._timeout
            )
        except (OSError, asyncio.TimeoutError):
            self._reader = None
            self._writer = None
            return False
        self._reader = reader
        self._writer = writer
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        """Returns result dict; raises RuntimeError(code) on error responses."""
        if not self.is_connected:
            if not await self.connect():
                raise RuntimeError("NOT_CONNECTED")
        async with self._lock:
            reader, writer = self._reader, self._writer
            if reader is None or writer is None:
                raise RuntimeError("NOT_CONNECTED")
            req_id = self._next_id
            self._next_id += 1
            payload = json.dumps({"id": req_id, "method": method, "params": params or {}}) + "\n"
            try:
                writer.write(payload.encode("utf-8"))
                await asyncio.wait_for(writer.drain(), timeout=self._timeout)
                line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
            except (OSError, asyncio.TimeoutError) as exc:
                await self.close()
                raise RuntimeError("CONNECTION_ERROR") from exc
            if not line:
                await self.close()
                raise RuntimeError("CONNECTION_CLOSED")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("BAD_RESPONSE") from exc
        if data.get("ok"):
            return data.get("result") or {}
        error = data.get("error") or {}
        raise RuntimeError(error.get("code", "INTERNAL"))

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


class ContactNotePanel(Vertical):
    """Owner-written note about the selected contact.

    The note is saved per contact (daemon `contacts.set_description`) and
    appended to the AI's system prompt when replying to that contact —
    e.g. "my very strict mom so be nice to her".
    """

    DEFAULT_CSS = """
    ContactNotePanel {
        border: round $panel;
    }
    ContactNotePanel > #note-target {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ContactNotePanel > #note-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.border_title = "Contact Note"
        self.current_guid: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Select a contact to add a note.", id="note-target")
        yield Input(
            placeholder='e.g. "my very strict mom so be nice to her"',
            max_length=500,
            id="note-input",
            disabled=True,
        )
        yield Static("Enter saves. The note is added to the AI prompt for this contact.", id="note-hint")

    def show_contact(self, contact: dict | None) -> None:
        """Point the note editor at a contact (or clear it for None/groups)."""
        target = self.query_one("#note-target", Static)
        note_input = self.query_one("#note-input", Input)
        if contact is None:
            self.current_guid = None
            target.update("Select a contact to add a note.")
            note_input.value = ""
            note_input.disabled = True
            return
        if contact.get("is_group"):
            self.current_guid = None
            name = contact.get("display_name") or contact.get("address") or "(unknown)"
            target.update(f"{name} — group chats cannot have notes.")
            note_input.value = ""
            note_input.disabled = True
            return
        self.current_guid = contact["chat_guid"]
        name = contact.get("display_name") or contact.get("address") or "(unknown)"
        target.update(f"Note for {name}:")
        note_input.value = str(contact.get("description") or "")
        note_input.disabled = False

class _HeaderBar(Horizontal):
    """'TextForMe' left, 'Service: Running/Stopped' right."""

    DEFAULT_CSS = """
    _HeaderBar {
        height: 1;
        background: $primary;
        color: $text;
    }
    _HeaderBar > #app-title {
        width: auto;
        padding: 0 1;
        text-style: bold;
    }
    _HeaderBar > #service-status {
        width: 1fr;
        content-align: right middle;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("TextForMe", id="app-title")
        yield Static("Service: Stopped", id="service-status")


class TextForMeApp(App[None]):
    """The whole TUI: header, contacts table, settings panel, footer."""

    TITLE = "TextForMe"

    CSS = """
    * {
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-color: $panel;
        scrollbar-color-hover: $primary;
        scrollbar-color-active: $primary;
        scrollbar-corner-color: $surface;
    }
    #body {
        height: 1fr;
    }
    ContactsTable {
        width: 2fr;
        border: round $panel;
        scrollbar-size-vertical: 0;
    }
    #right-col {
        width: 3fr;
        scrollbar-size-vertical: 0;
    }
    SettingsPanel {
        height: 12;
        border: round $panel;
        scrollbar-size-vertical: 0;
    }
    ContactNotePanel {
        height: 1fr;
        scrollbar-size-vertical: 0;
    }
    #hint {
        height: 1;
        color: $warning;
        content-align: center middle;
    }
    #footer-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=False),
        Binding("s", "save", "Save", show=False),
        Binding("L", "show_logs", "Logs", show=False),
        Binding("tab", "focus_next", "Settings", show=False),
    ]

    def __init__(self, client: DaemonClient | None = None, poll_interval: float = 3.0) -> None:
        super().__init__()
        self.client = client or DaemonClient()
        self._poll_interval = poll_interval
        self._connected = False
        self._model_names: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield _HeaderBar()
        with Horizontal(id="body"):
            yield ContactsTable(id="contacts")
            with Vertical(id="right-col"):
                yield SettingsPanel(id="settings")
                yield ContactNotePanel(id="contact-note")
        yield Static("", id="hint")
        yield Static(_FOOTER_TEXT, id="footer-bar")

    async def on_mount(self) -> None:
        self.query_one(ContactsTable).focus()
        await self._refresh_connection()
        self.set_interval(self._poll_interval, self._poll)

    async def _poll(self) -> None:
        if not self._connected:
            await self._refresh_connection()

    async def _refresh_connection(self) -> None:
        connected = await self.client.connect()
        if connected:
            await self._load_all()
        else:
            self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected
        try:
            status = self.query_one("#service-status", Static)
            hint = self.query_one("#hint", Static)
        except Exception:
            return
        contacts = self.query_one(ContactsTable)
        settings_panel = self.query_one(SettingsPanel)
        note_panel = self.query_one(ContactNotePanel)
        if connected:
            status.update("Service: Running")
            hint.update("")
            contacts.disabled = False
            settings_panel.disabled = False
            note_panel.disabled = False
        else:
            status.update("Service: Stopped")
            hint.update("run: textforme install")
            contacts.disabled = True
            settings_panel.disabled = True
            note_panel.disabled = True

    async def _load_all(self) -> None:
        try:
            await self.client.request("status")
            contacts_result = await self.client.request("contacts.list")
            settings_result = await self.client.request("settings.get")
        except RuntimeError:
            self._set_connected(False)
            return
        self._set_connected(True)
        # Best-effort: resolve the selected model id to its display name.
        models = await self._fetch_models(notify_errors=False)
        self.query_one(ContactsTable).load(contacts_result.get("contacts", []))
        self.query_one(SettingsPanel).load(
            settings_result.get("settings", {}),
            has_api_key(),
            model_names=self._model_names if models else None,
        )

    async def _fetch_models(self, notify_errors: bool) -> list[dict[str, str]]:
        """models.list via the daemon; updates the id->display_name cache."""
        try:
            result = await self.client.request("models.list")
        except RuntimeError as exc:
            code = str(exc)
            if notify_errors:
                if code == "NO_API_KEY":
                    self.notify("No API key configured — re-run onboarding first.", severity="warning")
                else:
                    self.notify(f"Could not fetch models: {code}", severity="error")
            if code in _CONNECTION_ERRORS:
                self._set_connected(False)
            return []
        models = result.get("models", [])
        if models:
            self._model_names = {m["model_id"]: m["display_name"] for m in models}
        return models

    async def on_contacts_table_toggled(self, message: ContactsTable.Toggled) -> None:
        message.stop()
        try:
            await self.client.request(
                "contacts.set_ai", {"chat_guid": message.chat_guid, "enabled": message.enabled}
            )
        except RuntimeError as exc:
            code = str(exc)
            self.query_one(ContactsTable).mark(message.chat_guid, not message.enabled)
            if code == "GROUP_FORBIDDEN":
                self.notify("Group chats cannot have AI enabled.", severity="warning")
            else:
                self.notify(f"Could not update contact: {code}", severity="error")
            if code in _CONNECTION_ERRORS:
                self._set_connected(False)

    async def on_settings_panel_changed(self, message: SettingsPanel.Changed) -> None:
        message.stop()
        try:
            await self.client.request("settings.set", {"key": message.key, "value": message.value})
            settings_result = await self.client.request("settings.get")
        except RuntimeError as exc:
            code = str(exc)
            self.notify(f"Could not save setting: {code}", severity="error")
            if code in _CONNECTION_ERRORS:
                self._set_connected(False)
            return
        self.query_one(SettingsPanel).load(settings_result.get("settings", {}), has_api_key())

    async def on_settings_panel_model_pick_requested(
        self, message: SettingsPanel.ModelPickRequested
    ) -> None:
        message.stop()
        models = await self._fetch_models(notify_errors=True)
        if not models:
            if self._connected:
                self.notify("No models available for this API key.", severity="warning")
            return
        current = self.query_one(SettingsPanel).current_value("selected_model_id")

        def _apply(model_id: str | None) -> None:
            if model_id and model_id != current:
                self.post_message(SettingsPanel.Changed("selected_model_id", model_id))

        self.push_screen(ModelPickerModal(models, current), _apply)

    async def on_settings_panel_model_cycle_requested(
        self, message: SettingsPanel.ModelCycleRequested
    ) -> None:
        message.stop()
        models = await self._fetch_models(notify_errors=True)
        if not models:
            return
        ids = [m["model_id"] for m in models]
        current = self.query_one(SettingsPanel).current_value("selected_model_id")
        try:
            new_id = ids[(ids.index(current) + message.delta) % len(ids)]
        except ValueError:
            new_id = ids[0]
        self.post_message(SettingsPanel.Changed("selected_model_id", new_id))

    def action_save(self) -> None:
        # Edits already apply immediately via settings.set / contacts.set_ai;
        # 'S' just gives explicit confirmation per the PRD footer contract.
        if self._connected:
            self.notify("All changes are saved.", severity="information")
        else:
            self.notify("Not connected to the service.", severity="warning")

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()


def run_app() -> None:
    """Blocking entry used by cli.main() after onboarding is complete."""
    TextForMeApp().run()
