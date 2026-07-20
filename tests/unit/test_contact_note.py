"""Tests for the per-contact note box in the TUI (replaces the log panel)."""

from __future__ import annotations

from textual.widgets import Input, Static

from textforme.tui.app import ContactNotePanel, TextForMeApp
from textforme.tui.contacts import ContactsTable


class FakeClient:
    """Stands in for DaemonClient: canned responses + call recording."""

    def __init__(self, contacts: list[dict]) -> None:
        self.contacts = contacts
        self.calls: list[tuple[str, dict]] = []
        self.is_connected = True

    async def connect(self) -> bool:
        return True

    async def request(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {}))
        if method == "contacts.list":
            return {"contacts": self.contacts}
        if method == "settings.get":
            return {"settings": {}}
        return {}

    async def close(self) -> None:
        pass


def _contact(guid: str, name: str, **extra) -> dict:
    return {
        "chat_guid": guid,
        "chat_id": 1,
        "display_name": name,
        "address": "+15550001111",
        "service": "iMessage",
        "is_group": False,
        "ai_enabled": True,
        "description": "",
        **extra,
    }


async def test_note_box_shows_selected_contact_and_saves():
    client = FakeClient([_contact("c1", "Mom", description="strict")])
    app = TextForMeApp(client=client, poll_interval=60.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ContactNotePanel)
        note_input = panel.query_one("#note-input", Input)
        assert panel.current_guid == "c1"
        assert "Mom" in str(panel.query_one("#note-target", Static).render())
        assert note_input.value == "strict"

        note_input.focus()
        note_input.value = "my very strict mom so be nice to her"
        await pilot.press("enter")
        await pilot.pause()

        assert (
            "contacts.set_description",
            {"chat_guid": "c1", "description": "my very strict mom so be nice to her"},
        ) in client.calls
        assert app.query_one(ContactsTable).contact("c1")["description"] == (
            "my very strict mom so be nice to her"
        )


async def test_note_box_disabled_for_group_chats():
    client = FakeClient(
        [_contact("grp1", "Family", is_group=True, ai_enabled=False)]
    )
    app = TextForMeApp(client=client, poll_interval=60.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(ContactNotePanel)
        assert panel.current_guid is None
        assert panel.query_one("#note-input", Input).disabled
