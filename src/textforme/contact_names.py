"""Local macOS Address Book contact-name resolver. Owner: Agent 2 (daemon).

`imsg chats.list` can return a resolved `name`/`contact_name` when Apple's
Contacts permission has been granted to whatever process runs `imsg rpc`,
but that permission is separate from (and not guaranteed alongside) Full
Disk Access, so direct chats frequently arrive with only a raw phone number
or email as their display name (see ARCHITECTURE.md §3, messaging/models.py
Chat.from_json).

This module is a local, best-effort fallback: it reads the macOS
AddressBook SQLite databases directly (read-only) and builds an
address -> "First Last" (or organization) map that daemon._sync_contacts /
onboarding._sync_contacts can consult when imsg didn't provide a name.

Must never raise. Any I/O or SQLite failure (missing file, no Full Disk
Access, unreadable/corrupt database, unexpected schema, ...) degrades to an
empty map so a lack of permission never breaks contact sync -- it just
means names stay unresolved until the user grants access.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _addressbook_paths() -> list[Path]:
    """Every AddressBook-v22.abcddb this user has: the top-level "me" database
    plus one per linked source (iCloud, Exchange, on-device, ...)."""
    paths: list[Path] = []
    try:
        base = Path.home() / "Library" / "Application Support" / "AddressBook"

        top_level = base / "AddressBook-v22.abcddb"
        if top_level.exists():
            paths.append(top_level)

        sources_dir = base / "Sources"
        if sources_dir.exists():
            for candidate in sorted(sources_dir.glob("*/AddressBook-v22.abcddb")):
                paths.append(candidate)
    except OSError:
        return paths
    return paths


def _normalize_phone(raw: str) -> str:
    """Digits only, e.g. '+1 (416) 727-2401' -> '14167272401'."""
    return "".join(ch for ch in raw if ch.isdigit())


def _record_name(first: object, last: object, org: object) -> str | None:
    """'First Last' when either name part is present, else the organization,
    else None (no usable name on this record)."""
    first_s = str(first).strip() if first else ""
    last_s = str(last).strip() if last else ""
    org_s = str(org).strip() if org else ""

    full = " ".join(part for part in (first_s, last_s) if part)
    if full:
        return full
    if org_s:
        return org_s
    return None


def _load_one(path: Path, names: dict[str, str]) -> None:
    """Merge one AddressBook database's phone/email -> name mappings into
    `names`. Lets sqlite3.Error / OSError propagate to the caller, which is
    responsible for degrading gracefully per-source."""
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cursor = conn.cursor()

        record_names: dict[int, str] = {}
        cursor.execute("SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD")
        for pk, first, last, org in cursor.fetchall():
            name = _record_name(first, last, org)
            if name:
                record_names[pk] = name

        cursor.execute("SELECT ZFULLNUMBER, ZOWNER FROM ZABCDPHONENUMBER")
        for full_number, owner in cursor.fetchall():
            name = record_names.get(owner)
            if not name or not full_number:
                continue
            digits = _normalize_phone(str(full_number))
            if not digits:
                continue
            names[digits] = name
            if len(digits) > 10:
                names[digits[-10:]] = name

        cursor.execute("SELECT ZADDRESS, ZOWNER FROM ZABCDEMAILADDRESS")
        for email, owner in cursor.fetchall():
            name = record_names.get(owner)
            if not name or not email:
                continue
            names[str(email).strip().lower()] = name
    finally:
        conn.close()


def load_contact_names() -> dict[str, str]:
    """Best-effort address -> saved contact name map, built from every local
    AddressBook source. Returns {} (never raises) if the AddressBook can't be
    read at all, e.g. no Full Disk Access / Contacts permission yet."""
    names: dict[str, str] = {}
    try:
        paths = _addressbook_paths()
    except (OSError, sqlite3.Error):
        return {}

    for path in paths:
        try:
            _load_one(path, names)
        except (OSError, sqlite3.Error):
            # One unreadable/corrupt source shouldn't blank out names already
            # collected from another source.
            continue

    return names


def resolve(address: str, names: dict[str, str]) -> str | None:
    """Look up a saved contact name for a raw phone number or email address.

    Emails match case-insensitively. Phone numbers are matched on their full
    digit string, falling back to the last 10 digits (handles +1 / area-code
    formatting differences between imsg's raw address and the Address Book
    entry). Returns None on no match -- callers should fall back to "" or the
    raw address themselves.
    """
    if not address:
        return None
    candidate = address.strip()
    if "@" in candidate:
        return names.get(candidate.lower())

    digits = _normalize_phone(candidate)
    if not digits:
        return None
    if digits in names:
        return names[digits]
    if len(digits) > 10:
        return names.get(digits[-10:])
    return None
