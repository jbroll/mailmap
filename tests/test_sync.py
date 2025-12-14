"""Tests for sync and transfer commands."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mailmap.commands.utils import sync_transfers
from mailmap.config import Config, DatabaseConfig, ImapConfig
from mailmap.database import Database, Email


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config with categories file."""
    categories_file = tmp_path / "categories.txt"
    categories_file.write_text("Work: Work emails\nPersonal: Personal stuff\n")

    return Config(
        imap=ImapConfig(
            host="imap.example.com",
            port=993,
            username="user",
            password="pass",
        ),
        database=DatabaseConfig(
            path=str(tmp_path / "test.db"),
            categories_file=str(categories_file),
        ),
    )


@pytest.fixture
def db_with_emails(tmp_path):
    """Create a database with test emails."""
    db = Database(str(tmp_path / "test.db"))
    db.connect()
    db.init_schema()

    # Add some test emails
    emails = [
        Email(
            message_id="<msg1@example.com>",
            folder_id="INBOX",
            subject="Work email 1",
            from_addr="boss@example.com",
            mbox_path="",
            classification="Work",
            confidence=0.9,
            processed_at=datetime.now(),
        ),
        Email(
            message_id="<msg2@example.com>",
            folder_id="INBOX",
            subject="Personal email",
            from_addr="friend@example.com",
            mbox_path="",
            classification="Personal",
            confidence=0.85,
            processed_at=datetime.now(),
        ),
        Email(
            message_id="<msg3@example.com>",
            folder_id="INBOX",
            subject="Work email 2",
            from_addr="coworker@example.com",
            mbox_path="",
            classification="Work",
            confidence=0.95,
            processed_at=datetime.now(),
        ),
    ]

    for email in emails:
        db.insert_email(email)

    # Mark one as already transferred
    db.mark_as_transferred("<msg1@example.com>")

    db.close()
    return Database(str(tmp_path / "test.db"))


class TestSyncTransfers:
    """Tests for sync_transfers function."""

    def test_sync_clears_and_rescans(self, mock_config, db_with_emails):
        """Test that sync clears transfers and rescans IMAP folders."""
        mock_mailbox = MagicMock()
        mock_mailbox.list_folders.return_value = ["Work", "Personal", "INBOX"]
        mock_mailbox.fetch_all_message_ids.side_effect = lambda folder: {
            "Work": ["<msg1@example.com>", "<msg3@example.com>"],
            "Personal": ["<msg2@example.com>"],
        }.get(folder, [])

        with patch("mailmap.imap_client.ImapMailbox", return_value=mock_mailbox):
            sync_transfers(mock_config, db_with_emails, dry_run=False)

        db_with_emails.connect()
        try:
            # All three should now be marked as transferred
            assert db_with_emails.get_transferred_count() == 3
        finally:
            db_with_emails.close()

    def test_sync_dry_run_does_not_modify(self, mock_config, db_with_emails):
        """Test that dry run doesn't modify the database."""
        db_with_emails.connect()
        before_count = db_with_emails.get_transferred_count()
        db_with_emails.close()

        mock_mailbox = MagicMock()
        mock_mailbox.list_folders.return_value = ["Work", "Personal"]
        mock_mailbox.fetch_all_message_ids.return_value = ["<msg1@example.com>"]

        with patch("mailmap.imap_client.ImapMailbox", return_value=mock_mailbox):
            sync_transfers(mock_config, db_with_emails, dry_run=True)

        db_with_emails.connect()
        try:
            # Should still have the original count
            assert db_with_emails.get_transferred_count() == before_count
        finally:
            db_with_emails.close()

    def test_sync_handles_missing_folders(self, mock_config, db_with_emails):
        """Test that sync handles folders that don't exist on server."""
        mock_mailbox = MagicMock()
        # Only Work folder exists on server
        mock_mailbox.list_folders.return_value = ["Work", "INBOX"]
        mock_mailbox.fetch_all_message_ids.return_value = ["<msg1@example.com>"]

        with patch("mailmap.imap_client.ImapMailbox", return_value=mock_mailbox):
            sync_transfers(mock_config, db_with_emails, dry_run=False)

        db_with_emails.connect()
        try:
            # Only msg1 should be marked
            assert db_with_emails.get_transferred_count() == 1
        finally:
            db_with_emails.close()

    def test_sync_handles_empty_folders(self, mock_config, db_with_emails):
        """Test that sync handles empty folders gracefully."""
        mock_mailbox = MagicMock()
        mock_mailbox.list_folders.return_value = ["Work", "Personal"]
        mock_mailbox.fetch_all_message_ids.return_value = []

        with patch("mailmap.imap_client.ImapMailbox", return_value=mock_mailbox):
            sync_transfers(mock_config, db_with_emails, dry_run=False)

        db_with_emails.connect()
        try:
            assert db_with_emails.get_transferred_count() == 0
        finally:
            db_with_emails.close()

    def test_sync_handles_fetch_error(self, mock_config, db_with_emails):
        """Test that sync continues when folder fetch fails."""
        mock_mailbox = MagicMock()
        mock_mailbox.list_folders.return_value = ["Work", "Personal"]
        mock_mailbox.fetch_all_message_ids.side_effect = [
            Exception("Connection error"),  # Work fails
            ["<msg2@example.com>"],  # Personal succeeds
        ]

        with patch("mailmap.imap_client.ImapMailbox", return_value=mock_mailbox):
            sync_transfers(mock_config, db_with_emails, dry_run=False)

        db_with_emails.connect()
        try:
            # Only msg2 should be marked
            assert db_with_emails.get_transferred_count() == 1
        finally:
            db_with_emails.close()

    def test_sync_with_no_categories(self, tmp_path):
        """Test that sync exits early with no categories."""
        categories_file = tmp_path / "categories.txt"
        categories_file.write_text("")  # Empty file

        config = Config(
            imap=ImapConfig(
                host="imap.example.com",
                port=993,
                username="user",
                password="pass",
            ),
            database=DatabaseConfig(
                path=str(tmp_path / "test.db"),
                categories_file=str(categories_file),
            ),
        )

        db = Database(str(tmp_path / "test.db"))

        # Should return early without connecting to IMAP
        with patch("mailmap.imap_client.ImapMailbox") as mock_imap:
            sync_transfers(config, db, dry_run=False)
            mock_imap.assert_not_called()
