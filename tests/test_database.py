"""Tests for database module."""

from datetime import datetime

import pytest

from mailmap.database import Database, Folder, Email


class TestDatabase:
    def test_connect_and_init(self, temp_dir):
        db = Database(temp_dir / "test.db")
        db.connect()
        db.init_schema()
        assert db._conn is not None
        db.close()
        assert db._conn is None

    def test_conn_property_raises_when_not_connected(self, temp_dir):
        db = Database(temp_dir / "test.db")
        with pytest.raises(RuntimeError, match="Database not connected"):
            _ = db.conn


class TestFolderOperations:
    def test_upsert_and_get_folder(self, test_db):
        folder = Folder(
            folder_id="INBOX",
            name="Inbox",
            description="Main inbox folder",
            last_updated=datetime.now(),
        )
        test_db.upsert_folder(folder)

        retrieved = test_db.get_folder("INBOX")
        assert retrieved is not None
        assert retrieved.folder_id == "INBOX"
        assert retrieved.name == "Inbox"
        assert retrieved.description == "Main inbox folder"

    def test_upsert_updates_existing(self, test_db):
        folder = Folder(folder_id="INBOX", name="Inbox", description="Original")
        test_db.upsert_folder(folder)

        folder.description = "Updated description"
        test_db.upsert_folder(folder)

        retrieved = test_db.get_folder("INBOX")
        assert retrieved.description == "Updated description"

    def test_get_nonexistent_folder(self, test_db):
        result = test_db.get_folder("NONEXISTENT")
        assert result is None

    def test_get_all_folders(self, test_db):
        test_db.upsert_folder(Folder(folder_id="INBOX", name="Inbox"))
        test_db.upsert_folder(Folder(folder_id="Sent", name="Sent"))
        test_db.upsert_folder(Folder(folder_id="Trash", name="Trash"))

        folders = test_db.get_all_folders()
        assert len(folders) == 3
        folder_ids = {f.folder_id for f in folders}
        assert folder_ids == {"INBOX", "Sent", "Trash"}

    def test_get_folder_descriptions(self, test_db):
        test_db.upsert_folder(Folder(folder_id="INBOX", name="Inbox", description="Main inbox"))
        test_db.upsert_folder(Folder(folder_id="Sent", name="Sent", description="Sent mail"))
        test_db.upsert_folder(Folder(folder_id="Drafts", name="Drafts"))  # No description

        descriptions = test_db.get_folder_descriptions()
        assert descriptions == {
            "INBOX": "Main inbox",
            "Sent": "Sent mail",
        }


class TestEmailOperations:
    def test_insert_and_get_email(self, test_db):
        test_db.upsert_folder(Folder(folder_id="INBOX", name="Inbox"))

        email = Email(
            message_id="<test123@example.com>",
            folder_id="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            mbox_path="/path/to/mbox",
            processed_at=datetime.now(),
        )
        test_db.insert_email(email)

        retrieved = test_db.get_email("<test123@example.com>")
        assert retrieved is not None
        assert retrieved.message_id == "<test123@example.com>"
        assert retrieved.subject == "Test Subject"
        assert retrieved.from_addr == "sender@example.com"
        assert retrieved.mbox_path == "/path/to/mbox"

    def test_get_nonexistent_email(self, test_db):
        result = test_db.get_email("<nonexistent@example.com>")
        assert result is None

    def test_update_classification(self, test_db):
        test_db.upsert_folder(Folder(folder_id="INBOX", name="Inbox"))

        email = Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Test",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email)

        test_db.update_classification("<test@example.com>", "Receipts", 0.95)

        retrieved = test_db.get_email("<test@example.com>")
        assert retrieved.classification == "Receipts"
        assert retrieved.confidence == 0.95
        assert retrieved.processed_at is not None

    def test_get_unclassified_emails(self, test_db):
        test_db.upsert_folder(Folder(folder_id="INBOX", name="Inbox"))

        # Insert classified email
        email1 = Email(
            message_id="<classified@example.com>",
            folder_id="INBOX",
            subject="Classified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
            classification="Work",
            confidence=0.9,
        )
        test_db.insert_email(email1)

        # Insert unclassified email
        email2 = Email(
            message_id="<unclassified@example.com>",
            folder_id="INBOX",
            subject="Unclassified",
            from_addr="test@test.com",
            mbox_path="/path/to/mbox",
        )
        test_db.insert_email(email2)

        unclassified = test_db.get_unclassified_emails()
        assert len(unclassified) == 1
        assert unclassified[0].message_id == "<unclassified@example.com>"
