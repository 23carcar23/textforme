"""Contacts table widget (AI ON/OFF | name | number). Owner: Agent 3.

Groups: shown greyed-out as OFF and cannot be toggled (or hidden entirely).
Toggle -> daemon `contacts.set_ai` immediately; revert the cell on error.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable


class ContactsTable(DataTable):
    """AI | Contact | Number. Space toggles AI for non-group rows.

    This widget never talks to the daemon itself; toggling posts a
    ``ContactsTable.Toggled`` message that the app handles (making the
    daemon call and reverting the optimistic update on failure).
    """

    BINDINGS = [
        Binding("space", "toggle_ai", "Toggle", show=True),
        Binding("up", "cursor_up", "Move", show=True),
        Binding("down", "cursor_down", "Move", show=True),
    ]

    class Toggled(Message):
        """Posted when the user requests an AI toggle for a non-group contact."""

        def __init__(self, chat_guid: str, enabled: bool) -> None:
            self.chat_guid = chat_guid
            self.enabled = enabled
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._meta: dict[str, dict] = {}
        self._columns_built = False

    def on_mount(self) -> None:
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        if self._columns_built:
            return
        self.add_column("AI", key="ai", width=5)
        self.add_column("Contact", key="contact")
        self.add_column("Number", key="number")
        self._columns_built = True

    def load(self, contacts: list[dict]) -> None:
        """Replace all rows. Each contact dict has chat_guid, chat_id,
        display_name, address, service, is_group, ai_enabled."""
        self._ensure_columns()
        previous_guid = self._current_guid()
        self.clear()
        self._meta.clear()
        for contact in contacts:
            guid = contact["chat_guid"]
            self._meta[guid] = dict(contact)
            self.add_row(*self._render_row(contact), key=guid)
        if previous_guid is not None:
            self._restore_cursor(previous_guid)

    def _current_guid(self) -> str | None:
        if not self.row_count or self.cursor_row is None:
            return None
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return row_key.value

    def _restore_cursor(self, guid: str) -> None:
        for index, key in enumerate(self.rows.keys()):
            if key.value == guid:
                try:
                    self.move_cursor(row=index)
                except Exception:
                    pass
                return

    @staticmethod
    def _render_row(contact: dict) -> tuple[Text, Text, Text]:
        is_group = bool(contact.get("is_group"))
        name = contact.get("display_name") or contact.get("address") or "(unknown)"
        number = contact.get("address", "")
        if is_group:
            return (
                Text("--", style="dim"),
                Text(f"{name} (group)", style="dim"),
                Text(number, style="dim"),
            )
        enabled = bool(contact.get("ai_enabled"))
        ai_style = "bold green" if enabled else "grey58"
        return (
            Text("ON" if enabled else "OFF", style=ai_style),
            Text(str(name)),
            Text(str(number)),
        )

    def mark(self, chat_guid: str, enabled: bool) -> None:
        """Optimistically (or on revert) set the AI cell for a contact."""
        meta = self._meta.get(chat_guid)
        if meta is None:
            return
        meta["ai_enabled"] = enabled
        cells = self._render_row(meta)
        try:
            self.update_cell(chat_guid, "ai", cells[0])
        except Exception:
            pass

    def action_toggle_ai(self) -> None:
        if not self.row_count or self.cursor_row is None:
            return
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return
        guid = row_key.value
        if guid is None:
            return
        meta = self._meta.get(guid)
        if meta is None:
            return
        if meta.get("is_group"):
            self.notify("Group chats cannot have AI enabled.", severity="warning")
            return
        new_enabled = not bool(meta.get("ai_enabled"))
        self.mark(guid, new_enabled)  # optimistic update
        self.post_message(self.Toggled(guid, new_enabled))
