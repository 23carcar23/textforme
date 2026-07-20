"""Comprehensive tests for the Database module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from textforme.config import DEFAULT_SETTINGS, Settings
from textforme.database import ContactRecord, Database
from tests.fixtures.factories import make_contact, make_message, make_settings


class TestSchemaAndInit:
    """Test schema creation and idempotent reopening."""

    def test_schema_created_on_init(self, tmp_path: Path) -> None:
        """Schema tables should be created on first open."""
        db_path = tmp_path / "test.db"
        db = Database(db_path)

        # Check that all tables exist
        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}

        assert tables == {"schema_version", "contacts", "processed_messages", "settings"}
        db.close()

    def test_idempotent_reopen(self, tmp_path: Path) -> None:
        """Reopening an existing database should not error or recreate schema."""
        db_path = tmp_path / "test.db"

        # First open
        db1 = Database(db_path)
        cursor = db1._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        count1 = cursor.fetchone()[0]
        db1.close()

        # Second open
        db2 = Database(db_path)
        cursor = db2._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        count2 = cursor.fetchone()[0]
        db2.close()

        # Schema version should be consistent
        assert count1 == count2

    def test_schema_version_set(self, tmp_path: Path) -> None:
        """Schema version should be at the latest migration after initialization."""
        db_path = tmp_path / "test.db"
        db = Database(db_path)

        cursor = db._conn.cursor()
        cursor.execute("SELECT version FROM schema_version")
        version = cursor.fetchone()

        assert version is not None
        assert version[0] == 2
        db.close()


class TestSettings:
    """Test settings management."""

    def test_get_settings_returns_defaults(self, tmp_path: Path) -> None:
        """get_settings should return DEFAULT_SETTINGS when database is empty."""
        db = Database(tmp_path / "test.db")
        settings = db.get_settings()

        assert settings.selected_model_id == DEFAULT_SETTINGS["selected_model_id"]
        assert settings.global_ai_enabled is True  # 'true' string converts to True
        assert settings.paused is False
        assert settings.maximum_reply_length == 300
        db.close()

    def test_set_and_get_setting(self, tmp_path: Path) -> None:
        """set_setting should persist and get_settings should retrieve it."""
        db = Database(tmp_path / "test.db")

        db.set_setting("selected_model_id", "test-model-123")
        settings = db.get_settings()

        assert settings.selected_model_id == "test-model-123"
        db.close()

    def test_set_setting_unknown_key_raises_keyerror(self, tmp_path: Path) -> None:
        """set_setting should raise KeyError for unknown keys."""
        db = Database(tmp_path / "test.db")

        with pytest.raises(KeyError):
            db.set_setting("unknown_key", "value")

        db.close()

    def test_get_raw_settings_merges_defaults(self, tmp_path: Path) -> None:
        """get_raw_settings should merge database values with defaults."""
        db = Database(tmp_path / "test.db")
        db.set_setting("selected_model_id", "custom-model")

        raw = db.get_raw_settings()

        # Custom value should be present
        assert raw["selected_model_id"] == "custom-model"
        # Defaults should be present for unset keys
        assert "paused" in raw
        assert raw["paused"] == DEFAULT_SETTINGS["paused"]
        db.close()

    def test_set_setting_override_default(self, tmp_path: Path) -> None:
        """Setting a value should override the default."""
        db = Database(tmp_path / "test.db")

        db.set_setting("paused", "true")
        settings = db.get_settings()

        assert settings.paused is True
        db.close()


class TestContacts:
    """Test contact management."""

    def test_upsert_contact_insert(self, tmp_path: Path) -> None:
        """upsert_contact should insert a new contact."""
        db = Database(tmp_path / "test.db")
        contact = make_contact()

        db.upsert_contact(contact)
        retrieved = db.get_contact(contact.chat_guid)

        assert retrieved is not None
        assert retrieved.chat_guid == contact.chat_guid
        assert retrieved.display_name == contact.display_name
        assert retrieved.ai_enabled is True
        db.close()

    def test_upsert_contact_preserves_ai_enabled(self, tmp_path: Path) -> None:
        """upsert_contact should not overwrite existing ai_enabled."""
        db = Database(tmp_path / "test.db")
        contact1 = make_contact(ai_enabled=True)

        db.upsert_contact(contact1)

        # Upsert with ai_enabled=False
        contact2 = make_contact(
            chat_guid=contact1.chat_guid,
            display_name="Updated Name",
            ai_enabled=False,
        )
        db.upsert_contact(contact2)

        # ai_enabled should remain True
        retrieved = db.get_contact(contact1.chat_guid)
        assert retrieved is not None
        assert retrieved.ai_enabled is True
        assert retrieved.display_name == "Updated Name"
        db.close()

    def test_upsert_contact_updates_other_fields(self, tmp_path: Path) -> None:
        """upsert_contact should update other fields."""
        db = Database(tmp_path / "test.db")
        contact1 = make_contact(display_name="Old Name")

        db.upsert_contact(contact1)

        contact2 = make_contact(
            chat_guid=contact1.chat_guid,
            display_name="New Name",
            address="+9876543210",
        )
        db.upsert_contact(contact2)

        retrieved = db.get_contact(contact1.chat_guid)
        assert retrieved is not None
        assert retrieved.display_name == "New Name"
        assert retrieved.address == "+9876543210"
        db.close()

    def test_list_contacts_ordered(self, tmp_path: Path) -> None:
        """list_contacts should order by is_group ASC, then display_name COLLATE NOCASE."""
        db = Database(tmp_path / "test.db")

        db.upsert_contact(make_contact(chat_guid="g1", display_name="Zoe", is_group=False))
        db.upsert_contact(make_contact(chat_guid="g2", display_name="Alice", is_group=False))
        db.upsert_contact(make_contact(chat_guid="g3", display_name="Group", is_group=True))
        db.upsert_contact(make_contact(chat_guid="g4", display_name="Another Group", is_group=True))

        contacts = db.list_contacts()

        # Direct chats first, then groups
        assert contacts[0].display_name == "Alice"
        assert contacts[1].display_name == "Zoe"
        assert contacts[2].display_name == "Another Group"
        assert contacts[3].display_name == "Group"
        db.close()

    def test_get_contact_by_chat_id(self, tmp_path: Path) -> None:
        """get_contact_by_chat_id should retrieve by chat_id."""
        db = Database(tmp_path / "test.db")
        contact = make_contact(chat_id=123)

        db.upsert_contact(contact)
        retrieved = db.get_contact_by_chat_id(123)

        assert retrieved is not None
        assert retrieved.chat_id == 123
        db.close()

    def test_get_contact_by_chat_id_not_found(self, tmp_path: Path) -> None:
        """get_contact_by_chat_id should return None if not found."""
        db = Database(tmp_path / "test.db")

        retrieved = db.get_contact_by_chat_id(999)

        assert retrieved is None
        db.close()

    def test_get_contact_not_found(self, tmp_path: Path) -> None:
        """get_contact should return None if not found."""
        db = Database(tmp_path / "test.db")

        retrieved = db.get_contact("nonexistent-guid")

        assert retrieved is None
        db.close()

    def test_set_contact_ai_enabled(self, tmp_path: Path) -> None:
        """set_contact_ai should toggle ai_enabled."""
        db = Database(tmp_path / "test.db")
        contact = make_contact(ai_enabled=False)

        db.upsert_contact(contact)
        db.set_contact_ai(contact.chat_guid, True)

        retrieved = db.get_contact(contact.chat_guid)
        assert retrieved is not None
        assert retrieved.ai_enabled is True
        db.close()

    def test_set_contact_ai_disabled(self, tmp_path: Path) -> None:
        """set_contact_ai should disable ai_enabled."""
        db = Database(tmp_path / "test.db")
        contact = make_contact(ai_enabled=True)

        db.upsert_contact(contact)
        db.set_contact_ai(contact.chat_guid, False)

        retrieved = db.get_contact(contact.chat_guid)
        assert retrieved is not None
        assert retrieved.ai_enabled is False
        db.close()

    def test_set_contact_ai_unknown_raises_keyerror(self, tmp_path: Path) -> None:
        """set_contact_ai should raise KeyError for unknown contact."""
        db = Database(tmp_path / "test.db")

        with pytest.raises(KeyError):
            db.set_contact_ai("unknown-guid", True)

        db.close()

    def test_set_contact_ai_group_raises_valueerror(self, tmp_path: Path) -> None:
        """set_contact_ai should raise ValueError('GROUP_FORBIDDEN') for groups."""
        db = Database(tmp_path / "test.db")
        group_contact = make_contact(is_group=True)

        db.upsert_contact(group_contact)

        with pytest.raises(ValueError) as exc_info:
            db.set_contact_ai(group_contact.chat_guid, True)

        assert str(exc_info.value) == "GROUP_FORBIDDEN"
        db.close()

    def test_set_contact_ai_persists_across_reopen(self, tmp_path: Path) -> None:
        """ai_enabled setting should survive database close and reopen."""
        db_path = tmp_path / "test.db"
        contact = make_contact(ai_enabled=False)

        db1 = Database(db_path)
        db1.upsert_contact(contact)
        db1.set_contact_ai(contact.chat_guid, True)
        db1.close()

        # Reopen
        db2 = Database(db_path)
        retrieved = db2.get_contact(contact.chat_guid)

        assert retrieved is not None
        assert retrieved.ai_enabled is True
        db2.close()

    def test_set_contact_last_seen(self, tmp_path: Path) -> None:
        """set_contact_last_seen should update last_seen_message_guid."""
        db = Database(tmp_path / "test.db")
        contact = make_contact()

        db.upsert_contact(contact)
        db.set_contact_last_seen(contact.chat_guid, "new-message-guid")

        retrieved = db.get_contact(contact.chat_guid)
        assert retrieved is not None
        assert retrieved.last_seen_message_guid == "new-message-guid"
        db.close()


class TestProcessedMessages:
    """Test processed message tracking."""

    def test_is_processed_false_for_new(self, tmp_path: Path) -> None:
        """is_processed should return False for unseen messages."""
        db = Database(tmp_path / "test.db")

        result = db.is_processed("new-message-guid")

        assert result is False
        db.close()

    def test_is_processed_true_after_record(self, tmp_path: Path) -> None:
        """is_processed should return True after recording a message."""
        db = Database(tmp_path / "test.db")

        db.record_processed("msg-guid", "chat-guid", "replied")
        result = db.is_processed("msg-guid")

        assert result is True
        db.close()

    def test_record_processed_insert(self, tmp_path: Path) -> None:
        """record_processed should insert a new message record."""
        db = Database(tmp_path / "test.db")

        db.record_processed(
            "msg-guid",
            "chat-guid",
            "replied",
            error_code=None,
            reply_sent=True,
        )

        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT status, error_code, reply_sent_at FROM processed_messages WHERE message_guid = ?",
            ("msg-guid",),
        )
        row = cursor.fetchone()

        assert row is not None
        assert row[0] == "replied"
        assert row[1] is None
        assert row[2] is not None  # reply_sent_at should be set
        db.close()

    def test_record_processed_with_error_code(self, tmp_path: Path) -> None:
        """record_processed should store error_code."""
        db = Database(tmp_path / "test.db")

        db.record_processed(
            "msg-guid",
            "chat-guid",
            "failed",
            error_code="ANTHROPIC_TIMEOUT",
            reply_sent=False,
        )

        cursor = db._conn.cursor()
        cursor.execute(
            "SELECT status, error_code FROM processed_messages WHERE message_guid = ?",
            ("msg-guid",),
        )
        row = cursor.fetchone()

        assert row is not None
        assert row[0] == "failed"
        assert row[1] == "ANTHROPIC_TIMEOUT"
        db.close()

    def test_record_processed_dedup_by_message_guid(self, tmp_path: Path) -> None:
        """record_processed should upsert on message_guid collision."""
        db = Database(tmp_path / "test.db")

        db.record_processed("msg-guid", "chat-guid-1", "failed")
        db.record_processed("msg-guid", "chat-guid-2", "replied", reply_sent=True)

        cursor = db._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processed_messages")
        count = cursor.fetchone()[0]

        # Should have only one row
        assert count == 1

        cursor.execute(
            "SELECT status, chat_guid FROM processed_messages WHERE message_guid = ?",
            ("msg-guid",),
        )
        row = cursor.fetchone()

        # Should be updated with the second values
        assert row[0] == "replied"
        assert row[1] == "chat-guid-2"
        db.close()

    def test_replies_since(self, tmp_path: Path) -> None:
        """replies_since should count status='replied' rows with reply_sent_at >= since_iso."""
        db = Database(tmp_path / "test.db")

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=2)).isoformat()
        recent_time = (now - timedelta(minutes=30)).isoformat()

        # Record old replied message
        cursor = db._conn.cursor()
        cursor.execute(
            "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status, reply_sent_at) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "chat1", old_time, "replied", old_time),
        )

        # Record recent replied message
        db.record_processed("msg2", "chat1", "replied", reply_sent=True)

        # Record failed message
        db.record_processed("msg3", "chat1", "failed")

        # Count replies since 1 hour ago
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        count = db.replies_since(one_hour_ago)

        # Should count only the recent replied message
        assert count == 1
        db.close()

    def test_last_reply_at(self, tmp_path: Path) -> None:
        """last_reply_at should return the most recent reply_sent_at for a chat."""
        db = Database(tmp_path / "test.db")

        now = datetime.now(timezone.utc)
        time1 = (now - timedelta(hours=1)).isoformat()
        time2 = (now - timedelta(minutes=30)).isoformat()

        cursor = db._conn.cursor()
        cursor.execute(
            "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status, reply_sent_at) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "chat1", time1, "replied", time1),
        )
        cursor.execute(
            "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status, reply_sent_at) VALUES (?, ?, ?, ?, ?)",
            ("msg2", "chat1", time2, "replied", time2),
        )
        db._conn.commit()

        last_reply = db.last_reply_at("chat1")

        # Should return the most recent time
        assert last_reply is not None
        assert last_reply == time2
        db.close()

    def test_last_reply_at_none_for_no_replies(self, tmp_path: Path) -> None:
        """last_reply_at should return None if no replied messages."""
        db = Database(tmp_path / "test.db")

        db.record_processed("msg1", "chat1", "failed")
        db.record_processed("msg2", "chat1", "skipped:cooldown")

        last_reply = db.last_reply_at("chat1")

        assert last_reply is None
        db.close()

    def test_last_reply_at_none_for_unknown_chat(self, tmp_path: Path) -> None:
        """last_reply_at should return None for unknown chat_guid."""
        db = Database(tmp_path / "test.db")

        last_reply = db.last_reply_at("unknown-chat")

        assert last_reply is None
        db.close()

    def test_recent_consecutive_failures_counts_trailing_failed(self, tmp_path: Path) -> None:
        """recent_consecutive_failures should count trailing 'failed' rows."""
        db = Database(tmp_path / "test.db")

        cursor = db._conn.cursor()
        now = datetime.now(timezone.utc)

        # Insert in reverse chronological order (most recent first)
        for i in range(3):
            time_str = (now - timedelta(minutes=i)).isoformat()
            cursor.execute(
                "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status) VALUES (?, ?, ?, ?)",
                (f"msg{i}", "chat1", time_str, "failed"),
            )
        db._conn.commit()

        count = db.recent_consecutive_failures()

        assert count == 3
        db.close()

    def test_recent_consecutive_failures_stops_at_replied(self, tmp_path: Path) -> None:
        """recent_consecutive_failures should stop counting at 'replied'."""
        db = Database(tmp_path / "test.db")

        cursor = db._conn.cursor()
        now = datetime.now(timezone.utc)

        # Insert: failed (3), replied (1), failed (2)
        statuses = ["failed", "failed", "failed", "replied", "failed", "failed"]
        for i, status in enumerate(statuses):
            time_str = (now - timedelta(minutes=i)).isoformat()
            cursor.execute(
                "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status) VALUES (?, ?, ?, ?)",
                (f"msg{i}", "chat1", time_str, status),
            )
        db._conn.commit()

        count = db.recent_consecutive_failures()

        # Should count only the trailing 3 failed (stop at replied)
        assert count == 3
        db.close()

    def test_recent_consecutive_failures_skips_skipped_rows(self, tmp_path: Path) -> None:
        """recent_consecutive_failures should skip 'skipped:*' rows."""
        db = Database(tmp_path / "test.db")

        cursor = db._conn.cursor()
        now = datetime.now(timezone.utc)

        # Insert: failed (2), skipped, failed (2)
        # Ordered DESC: failed, failed, skipped, failed, failed
        # Skipped rows are ignored/passed over, so count = 4 (all failed rows)
        statuses = ["failed", "failed", "skipped:cooldown", "failed", "failed"]
        for i, status in enumerate(statuses):
            time_str = (now - timedelta(minutes=i)).isoformat()
            cursor.execute(
                "INSERT INTO processed_messages (message_guid, chat_guid, received_at, status) VALUES (?, ?, ?, ?)",
                (f"msg{i}", "chat1", time_str, status),
            )
        db._conn.commit()

        count = db.recent_consecutive_failures()

        # Should count all trailing failed rows, passing over skipped rows
        assert count == 4
        db.close()

    def test_recent_consecutive_failures_zero_when_no_failures(self, tmp_path: Path) -> None:
        """recent_consecutive_failures should return 0 when no trailing failures."""
        db = Database(tmp_path / "test.db")

        # Record failed first, then replied (so replied is most recent)
        db.record_processed("msg1", "chat1", "failed")
        db.record_processed("msg2", "chat1", "replied", reply_sent=True)

        count = db.recent_consecutive_failures()

        # Most recent message is 'replied', so trailing failures = 0
        assert count == 0
        db.close()

    def test_recent_consecutive_failures_empty_db(self, tmp_path: Path) -> None:
        """recent_consecutive_failures should return 0 for empty database."""
        db = Database(tmp_path / "test.db")

        count = db.recent_consecutive_failures()

        assert count == 0
        db.close()


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_full_workflow(self, tmp_path: Path) -> None:
        """Test a complete workflow: settings, contacts, and message tracking."""
        db = Database(tmp_path / "test.db")

        # Setup settings
        db.set_setting("selected_model_id", "claude-3-5-sonnet-20241022")
        db.set_setting("global_ai_enabled", "true")

        # Add contacts
        contact1 = make_contact(
            chat_guid="chat1",
            chat_id=1,
            display_name="Alice",
            ai_enabled=True,
        )
        contact2 = make_contact(
            chat_guid="chat2",
            chat_id=2,
            display_name="Bob",
            ai_enabled=False,
            is_group=True,
        )
        db.upsert_contact(contact1)
        db.upsert_contact(contact2)

        # Record message processing
        db.record_processed("msg1", "chat1", "replied", reply_sent=True)
        db.record_processed("msg2", "chat2", "skipped:group")

        # Verify state
        settings = db.get_settings()
        assert settings.selected_model_id == "claude-3-5-sonnet-20241022"

        contacts = db.list_contacts()
        assert len(contacts) == 2
        assert contacts[0].display_name == "Alice"  # Direct chats first

        assert db.is_processed("msg1") is True
        assert db.replies_since("2026-01-01T00:00:00+00:00") == 1

        db.close()

    def test_concurrent_like_operations(self, tmp_path: Path) -> None:
        """Test that lock protects concurrent-like operations."""
        db = Database(tmp_path / "test.db")

        # These operations should not interfere
        contact = make_contact()
        db.upsert_contact(contact)
        db.set_contact_ai(contact.chat_guid, False)
        db.record_processed("msg1", contact.chat_guid, "replied", reply_sent=True)

        retrieved = db.get_contact(contact.chat_guid)
        assert retrieved is not None
        assert retrieved.ai_enabled is False
        assert db.is_processed("msg1") is True

        db.close()


class TestContactDescriptions:
    """Per-contact owner-written descriptions (schema v2)."""

    def test_description_defaults_empty(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.upsert_contact(make_contact())
        retrieved = db.get_contact("test-guid-001")
        assert retrieved is not None
        assert retrieved.description == ""
        db.close()

    def test_set_and_get_description(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.upsert_contact(make_contact())
        db.set_contact_description("test-guid-001", "my very strict mom so be nice to her")
        assert db.get_contact("test-guid-001").description == "my very strict mom so be nice to her"
        assert db.get_contact_by_chat_id(1).description == "my very strict mom so be nice to her"
        assert db.list_contacts()[0].description == "my very strict mom so be nice to her"
        db.close()

    def test_set_description_unknown_contact_raises(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        with pytest.raises(KeyError):
            db.set_contact_description("nope", "hi")
        db.close()

    def test_upsert_preserves_description(self, tmp_path: Path) -> None:
        """Contact re-sync must never wipe an owner-written description."""
        db = Database(tmp_path / "test.db")
        db.upsert_contact(make_contact())
        db.set_contact_description("test-guid-001", "be nice")
        db.upsert_contact(make_contact(display_name="Renamed"))
        retrieved = db.get_contact("test-guid-001")
        assert retrieved.display_name == "Renamed"
        assert retrieved.description == "be nice"
        db.close()

    def test_migration_from_v1_adds_description_column(self, tmp_path: Path) -> None:
        """A v1 database (no description column) migrates cleanly on open."""
        import sqlite3

        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (1);
            CREATE TABLE contacts (
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
            CREATE TABLE processed_messages (
                message_guid TEXT PRIMARY KEY,
                chat_guid    TEXT NOT NULL,
                received_at  TEXT NOT NULL,
                status       TEXT NOT NULL,
                error_code   TEXT,
                reply_sent_at TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO contacts VALUES ('c1', 1, 'Bob', '+1', 'iMessage', 0, 1, NULL, '2026-01-01T00:00:00+00:00');
            """
        )
        conn.commit()
        conn.close()

        db = Database(path)
        retrieved = db.get_contact("c1")
        assert retrieved is not None
        assert retrieved.description == ""
        db.set_contact_description("c1", "old friend")
        assert db.get_contact("c1").description == "old friend"
        db.close()
