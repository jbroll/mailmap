"""Tests for IMAP/source management CLI commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailmap.commands.imap_ops import (
    copy_email_cmd,
    create_folder_cmd,
    delete_folder_cmd,
    list_emails_cmd,
    list_folders_cmd,
    move_email_cmd,
    read_email_cmd,
)
from mailmap.config import Config, DatabaseConfig, ImapConfig, OllamaConfig, ThunderbirdConfig
from mailmap.email import UnifiedEmail
from mailmap.imap_client import EmailMessage


@pytest.fixture
def config():
    """Create a test configuration."""
    return Config(
        imap=ImapConfig(
            host="imap.example.com",
            port=993,
            username="test@example.com",
            password="password",
        ),
        thunderbird=ThunderbirdConfig(),
        ollama=OllamaConfig(),
        database=DatabaseConfig(),
    )


@pytest.fixture
def mock_imap_mailbox():
    """Create a mock ImapMailbox."""
    with patch("mailmap.commands.imap_ops.ImapMailbox") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance


class TestListFoldersCmd:
    @pytest.mark.asyncio
    async def test_list_folders(self, config, capsys):
        """Test listing folders with counts."""
        mock_source = AsyncMock()
        mock_source.list_folders = AsyncMock(return_value=["INBOX", "Sent", "Archive"])

        async def mock_read_emails(folder, limit=None):
            emails = {
                "INBOX": [
                    UnifiedEmail(message_id="1", folder="INBOX", subject="Test1", from_addr="a@b.com", body_text="", source_type="imap", source_ref=1),
                    UnifiedEmail(message_id="2", folder="INBOX", subject="Test2", from_addr="c@d.com", body_text="", source_type="imap", source_ref=2),
                ],
                "Sent": [
                    UnifiedEmail(message_id="3", folder="Sent", subject="Test3", from_addr="e@f.com", body_text="", source_type="imap", source_ref=3),
                ],
                "Archive": [],
            }
            for email in emails.get(folder, []):
                yield email

        mock_source.read_emails = mock_read_emails
        mock_source.__aenter__ = AsyncMock(return_value=mock_source)
        mock_source.__aexit__ = AsyncMock(return_value=None)

        with patch("mailmap.sources.select_source", return_value=mock_source):
            await list_folders_cmd(config, "imap")

        captured = capsys.readouterr()
        assert "INBOX" in captured.out
        assert "Sent" in captured.out
        assert "Archive" in captured.out
        assert "2" in captured.out  # INBOX count
        assert "1" in captured.out  # Sent count
        assert "0" in captured.out  # Archive count


class TestListEmailsCmd:
    @pytest.mark.asyncio
    async def test_list_emails(self, config, capsys):
        """Test listing emails in a folder."""
        mock_source = AsyncMock()

        async def mock_read_emails(folder, limit=None):
            emails = [
                UnifiedEmail(
                    message_id="<test1@example.com>",
                    folder="INBOX",
                    subject="Hello World",
                    from_addr="sender@example.com",
                    body_text="Body",
                    source_type="imap",
                    source_ref=123,
                ),
                UnifiedEmail(
                    message_id="<test2@example.com>",
                    folder="INBOX",
                    subject="Another Email",
                    from_addr="other@example.com",
                    body_text="Body2",
                    source_type="imap",
                    source_ref=456,
                ),
            ]
            for email in emails[:limit] if limit else emails:
                yield email

        mock_source.read_emails = mock_read_emails
        mock_source.__aenter__ = AsyncMock(return_value=mock_source)
        mock_source.__aexit__ = AsyncMock(return_value=None)

        with patch("mailmap.sources.select_source", return_value=mock_source):
            await list_emails_cmd(config, "INBOX", "imap", limit=50)

        captured = capsys.readouterr()
        assert "123" in captured.out
        assert "456" in captured.out
        assert "sender@example.com" in captured.out
        assert "Hello World" in captured.out
        assert "Total: 2 emails" in captured.out

    @pytest.mark.asyncio
    async def test_list_emails_with_limit(self, config, capsys):
        """Test listing emails respects limit."""
        mock_source = AsyncMock()

        async def mock_read_emails(folder, limit=None):
            emails = [
                UnifiedEmail(message_id=f"<test{i}@example.com>", folder="INBOX", subject=f"Email {i}", from_addr="a@b.com", body_text="", source_type="imap", source_ref=i)
                for i in range(10)
            ]
            for email in emails[:limit] if limit else emails:
                yield email

        mock_source.read_emails = mock_read_emails
        mock_source.__aenter__ = AsyncMock(return_value=mock_source)
        mock_source.__aexit__ = AsyncMock(return_value=None)

        with patch("mailmap.sources.select_source", return_value=mock_source):
            await list_emails_cmd(config, "INBOX", "imap", limit=3)

        captured = capsys.readouterr()
        assert "Total: 3 emails" in captured.out


class TestReadEmailCmd:
    @pytest.mark.asyncio
    async def test_read_email(self, config, mock_imap_mailbox, capsys):
        """Test reading an email."""
        mock_imap_mailbox.fetch_email.return_value = EmailMessage(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="This is the email body.",
            uid=123,
        )

        await read_email_cmd(config, "INBOX", 123)

        mock_imap_mailbox.connect.assert_called_once()
        mock_imap_mailbox.fetch_email.assert_called_once_with(123, "INBOX")
        mock_imap_mailbox.disconnect.assert_called_once()

        captured = capsys.readouterr()
        assert "From: sender@example.com" in captured.out
        assert "Subject: Test Subject" in captured.out
        assert "This is the email body." in captured.out

    @pytest.mark.asyncio
    async def test_read_email_not_found(self, config, mock_imap_mailbox, capsys):
        """Test reading a non-existent email."""
        mock_imap_mailbox.fetch_email.return_value = None

        await read_email_cmd(config, "INBOX", 999)

        mock_imap_mailbox.fetch_email.assert_called_once_with(999, "INBOX")
        mock_imap_mailbox.disconnect.assert_called_once()


class TestCreateFolderCmd:
    def test_create_folder_new(self, config, mock_imap_mailbox):
        """Test creating a new folder."""
        mock_imap_mailbox.folder_exists.return_value = False

        create_folder_cmd(config, "NewFolder")

        mock_imap_mailbox.connect.assert_called_once()
        mock_imap_mailbox.folder_exists.assert_called_once_with("NewFolder")
        mock_imap_mailbox.create_folder.assert_called_once_with("NewFolder")
        mock_imap_mailbox.disconnect.assert_called_once()

    def test_create_folder_already_exists(self, config, mock_imap_mailbox):
        """Test creating a folder that already exists."""
        mock_imap_mailbox.folder_exists.return_value = True

        create_folder_cmd(config, "ExistingFolder")

        mock_imap_mailbox.folder_exists.assert_called_once_with("ExistingFolder")
        mock_imap_mailbox.create_folder.assert_not_called()
        mock_imap_mailbox.disconnect.assert_called_once()


class TestDeleteFolderCmd:
    def test_delete_folder(self, config, mock_imap_mailbox):
        """Test deleting a folder."""
        mock_imap_mailbox.folder_exists.return_value = True
        mock_imap_mailbox.client = MagicMock()

        delete_folder_cmd(config, "OldFolder")

        mock_imap_mailbox.connect.assert_called_once()
        mock_imap_mailbox.folder_exists.assert_called_once_with("OldFolder")
        mock_imap_mailbox.client.delete_folder.assert_called_once_with("OldFolder")
        mock_imap_mailbox.disconnect.assert_called_once()

    def test_delete_folder_not_found(self, config, mock_imap_mailbox):
        """Test deleting a non-existent folder."""
        mock_imap_mailbox.folder_exists.return_value = False
        mock_imap_mailbox.client = MagicMock()

        delete_folder_cmd(config, "NonExistent")

        mock_imap_mailbox.folder_exists.assert_called_once_with("NonExistent")
        mock_imap_mailbox.client.delete_folder.assert_not_called()
        mock_imap_mailbox.disconnect.assert_called_once()


class TestMoveEmailCmd:
    def test_move_email(self, config, mock_imap_mailbox):
        """Test moving an email."""
        move_email_cmd(config, "INBOX", 123, "Archive")

        mock_imap_mailbox.connect.assert_called_once()
        mock_imap_mailbox.ensure_folder.assert_called_once_with("Archive")
        mock_imap_mailbox.move_email.assert_called_once_with(123, "INBOX", "Archive")
        mock_imap_mailbox.disconnect.assert_called_once()


class TestCopyEmailCmd:
    def test_copy_email(self, config, mock_imap_mailbox):
        """Test copying an email."""
        mock_imap_mailbox.client = MagicMock()

        copy_email_cmd(config, "INBOX", 123, "Archive")

        mock_imap_mailbox.connect.assert_called_once()
        mock_imap_mailbox.ensure_folder.assert_called_once_with("Archive")
        mock_imap_mailbox.select_folder.assert_called_once_with("INBOX")
        mock_imap_mailbox.client.copy.assert_called_once_with([123], "Archive")
        mock_imap_mailbox.disconnect.assert_called_once()
