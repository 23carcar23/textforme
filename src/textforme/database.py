"""SQLite storage. Owner: Agent 5. Implement per docs/ARCHITECTURE.md §4.

Synchronous sqlite3, thread-safe via a lock; the daemon calls it from one
event loop so operations must be fast. All timestamps ISO 8601 UTC.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_SETTINGS, Settings


@dataclass
class ContactRecord:
    chat_guid: str
    chat_id: int
    display_name: str
    address: str
    service: str
    is_group: bool
    ai_enabled: bool
    last_seen_message_guid: str | None = None
    description: str = ""
    # "Realistic texting" per-contact toggle: when on, incoming bursts are
    # batched behind a random 0–3 minute timer instead of replied to one by one.
    reply_timer_enabled: bool = False


class Database:
    """All SQLite access. Creates schema (with schema_version migrations) on open."""

    def __init__(self, path: Path) -> None:
        """Open or create the SQLite database with schema migrations."""
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        """Initialize or migrate the database schema."""
        with self._lock:
            cursor = self._conn.cursor()

            # Ensure schema_version table exists
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                )"""
            )

            # Get current schema version (default to 0 if not set)
            cursor.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row else 0

            # Define all migrations (version, sql)
            migrations = [
                (
                    1,
                    """
                    CREATE TABLE IF NOT EXISTS contacts (
                        chat_guid   TEXT PRIMARY KEY,
                        chat_id     INTEGER NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        address     TEXT NOT NULL DEFAULT '',
                        service     TEXT NOT NULL DEFAULT 'iMessage',
                        is_group    INTEGER NOT NULL DEFAULT 0,
                        ai_enabled  INTEGER NOT NULL DEFAULT 0,
                        last_seen_message_guid TEXT,
                        updated_at  TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS processed_messages (
                        message_guid TEXT PRIMARY KEY,
                        chat_guid    TEXT NOT NULL,
                        received_at  TEXT NOT NULL,
                        status       TEXT NOT NULL,
                        error_code   TEXT,
                        reply_sent_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS settings (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """,
                ),
                (
                    2,
                    """
                    ALTER TABLE contacts ADD COLUMN description TEXT NOT NULL DEFAULT ''
                    """,
                ),
                (
                    3,
                    """
                    ALTER TABLE contacts ADD COLUMN reply_timer_enabled INTEGER NOT NULL DEFAULT 0
                    """,
                ),
            ]

            # Apply any pending migrations
            for version, sql in migrations:
                if version > current_version:
                    cursor.executescript(sql)
                    cursor.execute(
                        "DELETE FROM schema_version"
                    )  # Remove old version
                    cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                    self._conn.commit()

    # -- settings --------------------------------------------------------
    def get_settings(self) -> Settings:
        """Merged over config.DEFAULT_SETTINGS."""
        raw = self.get_raw_settings()
        return Settings.from_mapping(raw)

    def set_setting(self, key: str, value: str) -> None:
        """Upsert one key. Raises KeyError if key not in DEFAULT_SETTINGS."""
        if key not in DEFAULT_SETTINGS:
            raise KeyError(f"Unknown setting key: {key}")

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_raw_settings(self) -> dict[str, str]:
        """All keys (defaults merged) as strings, for the socket API."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            rows = cursor.fetchall()
            db_settings = {key: value for key, value in rows}

        # Merge with defaults
        result = {**DEFAULT_SETTINGS, **db_settings}
        return result

    # -- contacts --------------------------------------------------------
    def upsert_contact(self, contact: ContactRecord) -> None:
        """Insert or update; NEVER overwrites an existing row's ai_enabled."""
        updated_at = datetime.now(timezone.utc).isoformat()

        with self._lock:
            cursor = self._conn.cursor()

            # Check if contact exists to preserve ai_enabled
            cursor.execute("SELECT ai_enabled FROM contacts WHERE chat_guid = ?", (contact.chat_guid,))
            existing = cursor.fetchone()
            ai_enabled = existing[0] if existing else (1 if contact.ai_enabled else 0)

            cursor.execute(
                """INSERT INTO contacts
                (chat_guid, chat_id, display_name, address, service, is_group, ai_enabled, last_seen_message_guid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_guid) DO UPDATE SET
                chat_id=excluded.chat_id,
                display_name=excluded.display_name,
                address=excluded.address,
                service=excluded.service,
                is_group=excluded.is_group,
                last_seen_message_guid=excluded.last_seen_message_guid,
                updated_at=excluded.updated_at""",
                (
                    contact.chat_guid,
                    contact.chat_id,
                    contact.display_name,
                    contact.address,
                    contact.service,
                    1 if contact.is_group else 0,
                    ai_enabled,
                    contact.last_seen_message_guid,
                    updated_at,
                ),
            )
            self._conn.commit()

    def list_contacts(self) -> list[ContactRecord]:
        """Ordered: direct chats first, then by display_name."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT chat_guid, chat_id, display_name, address, service,
                is_group, ai_enabled, last_seen_message_guid, description,
                reply_timer_enabled
                FROM contacts
                ORDER BY is_group ASC, display_name COLLATE NOCASE ASC"""
            )
            rows = cursor.fetchall()

        return [self._row_to_contact(row) for row in rows]

    @staticmethod
    def _row_to_contact(row: tuple) -> ContactRecord:
        return ContactRecord(
            chat_guid=row[0],
            chat_id=row[1],
            display_name=row[2],
            address=row[3],
            service=row[4],
            is_group=bool(row[5]),
            ai_enabled=bool(row[6]),
            last_seen_message_guid=row[7],
            description=row[8] or "",
            reply_timer_enabled=bool(row[9]) if len(row) > 9 else False,
        )

    def get_contact_by_chat_id(self, chat_id: int) -> ContactRecord | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT chat_guid, chat_id, display_name, address, service,
                is_group, ai_enabled, last_seen_message_guid, description,
                reply_timer_enabled
                FROM contacts WHERE chat_id = ?""",
                (chat_id,),
            )
            row = cursor.fetchone()

        return self._row_to_contact(row) if row else None

    def get_contact(self, chat_guid: str) -> ContactRecord | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT chat_guid, chat_id, display_name, address, service,
                is_group, ai_enabled, last_seen_message_guid, description,
                reply_timer_enabled
                FROM contacts WHERE chat_guid = ?""",
                (chat_guid,),
            )
            row = cursor.fetchone()

        return self._row_to_contact(row) if row else None

    def set_contact_ai(self, chat_guid: str, enabled: bool) -> None:
        """Raises ValueError('GROUP_FORBIDDEN') for group chats; KeyError if unknown."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT is_group FROM contacts WHERE chat_guid = ?", (chat_guid,))
            row = cursor.fetchone()

            if not row:
                raise KeyError(f"Unknown contact: {chat_guid}")

            if bool(row[0]):
                raise ValueError("GROUP_FORBIDDEN")

            cursor.execute(
                "UPDATE contacts SET ai_enabled = ? WHERE chat_guid = ?",
                (1 if enabled else 0, chat_guid),
            )
            self._conn.commit()

    def set_contact_reply_timer(self, chat_guid: str, enabled: bool) -> None:
        """Toggle the per-contact realistic-texting reply timer.

        Raises ValueError('GROUP_FORBIDDEN') for group chats; KeyError if unknown.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT is_group FROM contacts WHERE chat_guid = ?", (chat_guid,))
            row = cursor.fetchone()

            if not row:
                raise KeyError(f"Unknown contact: {chat_guid}")

            if bool(row[0]):
                raise ValueError("GROUP_FORBIDDEN")

            cursor.execute(
                "UPDATE contacts SET reply_timer_enabled = ? WHERE chat_guid = ?",
                (1 if enabled else 0, chat_guid),
            )
            self._conn.commit()

    def set_contact_description(self, chat_guid: str, description: str) -> None:
        """Raises KeyError if the contact is unknown."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1 FROM contacts WHERE chat_guid = ?", (chat_guid,))
            if cursor.fetchone() is None:
                raise KeyError(f"Unknown contact: {chat_guid}")
            cursor.execute(
                "UPDATE contacts SET description = ? WHERE chat_guid = ?",
                (description, chat_guid),
            )
            self._conn.commit()

    def set_contact_last_seen(self, chat_guid: str, message_guid: str) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "UPDATE contacts SET last_seen_message_guid = ? WHERE chat_guid = ?",
                (message_guid, chat_guid),
            )
            self._conn.commit()

    # -- processed messages ---------------------------------------------
    def is_processed(self, message_guid: str) -> bool:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1 FROM processed_messages WHERE message_guid = ?", (message_guid,))
            return cursor.fetchone() is not None

    def record_processed(
        self,
        message_guid: str,
        chat_guid: str,
        status: str,
        error_code: str | None = None,
        reply_sent: bool = False,
    ) -> None:
        """Upsert; sets received_at (and reply_sent_at when reply_sent) to now UTC."""
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """INSERT INTO processed_messages
                (message_guid, chat_guid, received_at, status, error_code, reply_sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_guid) DO UPDATE SET
                chat_guid=excluded.chat_guid,
                received_at=excluded.received_at,
                status=excluded.status,
                error_code=excluded.error_code,
                reply_sent_at=CASE WHEN excluded.reply_sent_at IS NOT NULL THEN excluded.reply_sent_at ELSE reply_sent_at END""",
                (message_guid, chat_guid, now_iso, status, error_code, now_iso if reply_sent else None),
            )
            self._conn.commit()

    def replies_since(self, since_iso: str) -> int:
        """Count of status='replied' rows with reply_sent_at >= since_iso."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT COUNT(*) FROM processed_messages
                WHERE status = 'replied' AND reply_sent_at >= ?""",
                (since_iso,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def latest_reply_at(self) -> str | None:
        """Most recent reply_sent_at across all chats, ISO 8601 or None."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT MAX(reply_sent_at) FROM processed_messages
                WHERE status = 'replied' AND reply_sent_at IS NOT NULL"""
            )
            result = cursor.fetchone()
            return result[0] if result and result[0] else None

    def chats_with_replies_since(self, since_iso: str) -> list[str]:
        """Distinct chat_guids that received an AI reply after ``since_iso``,
        most recently active first. Pass '' to get every chat ever replied to."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT chat_guid, MAX(reply_sent_at) AS latest
                FROM processed_messages
                WHERE status = 'replied' AND reply_sent_at IS NOT NULL
                AND reply_sent_at > ?
                GROUP BY chat_guid
                ORDER BY latest DESC""",
                (since_iso,),
            )
            return [row[0] for row in cursor.fetchall()]

    def last_reply_at(self, chat_guid: str) -> str | None:
        """Most recent reply_sent_at for this chat, ISO 8601 or None."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT MAX(reply_sent_at) FROM processed_messages
                WHERE chat_guid = ? AND status = 'replied'""",
                (chat_guid,),
            )
            result = cursor.fetchone()
            return result[0] if result and result[0] else None

    def recent_consecutive_failures(self) -> int:
        """Length of the trailing run of status='failed' rows ordered by received_at
        (skipped rows are ignored, replied resets the run to 0)."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT status FROM processed_messages
                ORDER BY received_at DESC, rowid DESC"""
            )
            rows = cursor.fetchall()

        count = 0
        for i, (status,) in enumerate(rows):
            if status == "failed":
                count += 1
            elif status.startswith("skipped:"):
                # Skip over skipped rows, continue counting
                continue
            else:
                # Hit a non-skipped, non-failed status (e.g., 'replied')
                # If this is the first row, return 0 (no trailing failures)
                # Otherwise return the count we've accumulated
                if i == 0:
                    return 0
                else:
                    return count

        return count
