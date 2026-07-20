"""Unit tests for src/textforme/contact_names.py.

Builds a fake AddressBook-v22.abcddb (minimal ZABCDRECORD / ZABCDPHONENUMBER
/ ZABCDEMAILADDRESS schema) under tmp_path and points contact_names.Path.home()
at it, so these tests never touch the real macOS Address Book.
"""

from __future__ import annotations

import pathlib
import sqlite3
from pathlib import Path

import pytest

from textforme import contact_names
from textforme.messaging.models import Chat

from tests.unit.test_daemon import FakeDatabase, FakeImsgClient, make_daemon


# -- helpers ------------------------------------------------------------------


def _addressbook_db_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / "AddressBook" / "AddressBook-v22.abcddb"


def _addressbook_source_db_path(home: Path, source_id: str) -> Path:
    return (
        home
        / "Library"
        / "Application Support"
        / "AddressBook"
        / "Sources"
        / source_id
        / "AddressBook-v22.abcddb"
    )


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT)"
    )
    conn.execute("CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZFULLNUMBER TEXT, ZOWNER INTEGER)")
    conn.execute("CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZADDRESS TEXT, ZOWNER INTEGER)")


def _insert_record(
    conn: sqlite3.Connection,
    pk: int,
    first: str | None = None,
    last: str | None = None,
    org: str | None = None,
    phones: list[str] | None = None,
    emails: list[str] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION) VALUES (?, ?, ?, ?)",
        (pk, first, last, org),
    )
    for i, phone in enumerate(phones or []):
        conn.execute(
            "INSERT INTO ZABCDPHONENUMBER (Z_PK, ZFULLNUMBER, ZOWNER) VALUES (?, ?, ?)",
            (pk * 100 + i, phone, pk),
        )
    for i, email in enumerate(emails or []):
        conn.execute(
            "INSERT INTO ZABCDEMAILADDRESS (Z_PK, ZADDRESS, ZOWNER) VALUES (?, ?, ?)",
            (pk * 100 + i, email, pk),
        )


def _make_addressbook(path: Path, populate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        _create_schema(conn)
        populate(conn)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    return tmp_path


# -- load_contact_names: happy paths -------------------------------------------


def test_missing_addressbook_returns_empty(fake_home: Path) -> None:
    assert contact_names.load_contact_names() == {}


def test_phone_and_email_and_org_fallback(fake_home: Path) -> None:
    def populate(conn: sqlite3.Connection) -> None:
        _insert_record(conn, 1, first="Jane", last="Doe", phones=["+1 (416) 727-2401"])
        _insert_record(conn, 2, first="John", last="Smith", emails=["John@Example.com"])
        _insert_record(conn, 3, org="Acme Corp", phones=["4165551234"])
        # A record with no first/last/org at all contributes no name.
        _insert_record(conn, 4, phones=["4169990000"])

    _make_addressbook(_addressbook_db_path(fake_home), populate)

    names = contact_names.load_contact_names()

    # Punctuated +1 phone matches the bare 10-digit form.
    assert contact_names.resolve("+14167272401", names) == "Jane Doe"
    assert contact_names.resolve("4167272401", names) == "Jane Doe"

    # Email matching is case-insensitive.
    assert contact_names.resolve("john@example.com", names) == "John Smith"
    assert contact_names.resolve("JOHN@EXAMPLE.COM", names) == "John Smith"

    # Organization is used when there's no first/last name.
    assert contact_names.resolve("+14165551234", names) == "Acme Corp"

    # A record with no name data at all never appears.
    assert contact_names.resolve("+14169990000", names) is None

    # No match at all.
    assert contact_names.resolve("+19995550000", names) is None


def test_merges_multiple_addressbook_sources(fake_home: Path) -> None:
    def populate_top(conn: sqlite3.Connection) -> None:
        _insert_record(conn, 1, first="Jane", last="Doe", phones=["4167272401"])

    def populate_source(conn: sqlite3.Connection) -> None:
        _insert_record(conn, 1, first="Alex", last="Lee", emails=["alex@example.com"])

    _make_addressbook(_addressbook_db_path(fake_home), populate_top)
    _make_addressbook(_addressbook_source_db_path(fake_home, "ABCD1234"), populate_source)

    names = contact_names.load_contact_names()

    assert contact_names.resolve("4167272401", names) == "Jane Doe"
    assert contact_names.resolve("alex@example.com", names) == "Alex Lee"


# -- load_contact_names: graceful degradation ----------------------------------


def test_unreadable_database_returns_empty(fake_home: Path) -> None:
    path = _addressbook_db_path(fake_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real sqlite database")

    assert contact_names.load_contact_names() == {}


def test_one_bad_source_does_not_blank_out_a_good_one(fake_home: Path) -> None:
    def populate_top(conn: sqlite3.Connection) -> None:
        _insert_record(conn, 1, first="Jane", last="Doe", phones=["4167272401"])

    _make_addressbook(_addressbook_db_path(fake_home), populate_top)

    bad_path = _addressbook_source_db_path(fake_home, "BROKEN")
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"garbage")

    names = contact_names.load_contact_names()
    assert contact_names.resolve("4167272401", names) == "Jane Doe"


def test_no_home_directory_at_all_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point home() at a directory that doesn't even have a Library folder.
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "no-such-home")
    assert contact_names.load_contact_names() == {}


# -- resolve() edge cases ------------------------------------------------------


def test_resolve_empty_address_returns_none() -> None:
    assert contact_names.resolve("", {"4167272401": "Jane Doe"}) is None


def test_resolve_non_matching_short_digit_string_returns_none() -> None:
    assert contact_names.resolve("123", {"4167272401": "Jane Doe"}) is None


# -- daemon integration: _sync_contacts uses the resolver ----------------------


@pytest.mark.asyncio
async def test_daemon_sync_contacts_uses_resolver_for_unnamed_direct_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct chat with no imsg-provided name gets the Address Book name;
    imsg-provided names and group chats are left untouched."""
    canned = {"4167272401": "Jane Doe"}
    monkeypatch.setattr(contact_names, "load_contact_names", lambda: canned)

    chats = [
        # No name from imsg at all -> should resolve via Address Book.
        Chat(chat_id=1, guid="c1", identifier="+14167272401", display_name="", is_group=False),
        # imsg already provided a real name -> must not be overwritten.
        Chat(chat_id=2, guid="c2", identifier="+14167272401", display_name="Work Jane", is_group=False),
        # No address-book match -> falls back to empty string.
        Chat(chat_id=3, guid="c3", identifier="+19995550000", display_name="", is_group=False),
        # Group chat -> resolver must not be consulted even if the "address"
        # would otherwise match.
        Chat(chat_id=4, guid="c4", identifier="", participants=["+14167272401", "+19995550000"], display_name="", is_group=True),
    ]
    imsg = FakeImsgClient(chats=chats)
    db = FakeDatabase()
    daemon = make_daemon(database=db, imsg_client=imsg)

    count = await daemon._sync_contacts()

    assert count == 4
    assert db.get_contact("c1").display_name == "Jane Doe"
    assert db.get_contact("c2").display_name == "Work Jane"
    assert db.get_contact("c3").display_name == ""
    assert db.get_contact("c4").display_name == ""


@pytest.mark.asyncio
async def test_daemon_sync_contacts_calls_load_contact_names_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_load() -> dict[str, str]:
        calls.append(1)
        return {}

    monkeypatch.setattr(contact_names, "load_contact_names", fake_load)

    chats = [
        Chat(chat_id=1, guid="c1", identifier="+14167272401", display_name="", is_group=False),
        Chat(chat_id=2, guid="c2", identifier="+19995550000", display_name="", is_group=False),
    ]
    daemon = make_daemon(database=FakeDatabase(), imsg_client=FakeImsgClient(chats=chats))

    await daemon._sync_contacts()

    assert len(calls) == 1
