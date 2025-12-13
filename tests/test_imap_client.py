"""Tests for IMAP client module."""

from unittest.mock import MagicMock, patch
import email
from email.mime.text import MIMEText

import pytest

from mailmap.imap_client import (
    ImapMailbox,
    EmailMessage,
    decode_mime_header,
    extract_body,
)
from mailmap.config import ImapConfig


@pytest.fixture
def imap_config():
    """Create a test IMAP configuration."""
    return ImapConfig(
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="testpass",
    )


@pytest.fixture
def mock_imap_client():
    """Create a mock IMAPClient."""
    with patch("mailmap.imap_client.IMAPClient") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance


class TestDecodeMimeHeader:
    def test_decode_plain_header(self):
        result = decode_mime_header("Simple Subject")
        assert result == "Simple Subject"

    def test_decode_none_header(self):
        result = decode_mime_header(None)
        assert result == ""

    def test_decode_utf8_encoded_header(self):
        # RFC 2047 encoded header
        encoded = "=?UTF-8?B?SGVsbG8gV29ybGQ=?="
        result = decode_mime_header(encoded)
        assert result == "Hello World"

    def test_decode_mixed_header(self):
        # Mixed plain and encoded
        encoded = "Re: =?UTF-8?B?SGVsbG8=?= World"
        result = decode_mime_header(encoded)
        assert result == "Re: Hello World"


class TestExtractBody:
    def test_extract_plain_text_body(self):
        msg = MIMEText("This is the body text.", "plain", "utf-8")
        result = extract_body(msg)
        assert result == "This is the body text."

    def test_extract_body_from_bytes(self):
        raw = b"Content-Type: text/plain\r\n\r\nSimple body"
        msg = email.message_from_bytes(raw)
        result = extract_body(msg)
        assert "Simple body" in result

    def test_extract_empty_body(self):
        raw = b"Content-Type: text/plain\r\n\r\n"
        msg = email.message_from_bytes(raw)
        result = extract_body(msg)
        assert result == ""


class TestEmailMessage:
    def test_dataclass(self):
        msg = EmailMessage(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="Body content",
            uid=123,
        )
        assert msg.message_id == "<test@example.com>"
        assert msg.folder == "INBOX"
        assert msg.subject == "Test Subject"
        assert msg.uid == 123


class TestImapMailboxConnection:
    def test_connect(self, imap_config, mock_imap_client):
        mailbox = ImapMailbox(imap_config)
        mailbox.connect()

        assert mailbox._client is not None
        mock_imap_client.login.assert_called_once_with(
            imap_config.username, imap_config.password
        )

    def test_disconnect(self, imap_config, mock_imap_client):
        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        mailbox.disconnect()

        assert mailbox._client is None
        mock_imap_client.logout.assert_called_once()

    def test_client_property_raises_when_not_connected(self, imap_config):
        mailbox = ImapMailbox(imap_config)
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = mailbox.client

    def test_client_property_returns_client_when_connected(self, imap_config, mock_imap_client):
        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        assert mailbox.client is not None


class TestImapMailboxFolders:
    def test_list_folders(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
            ((), b"/", "Sent"),
            ((), b"/", "Drafts"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        folders = mailbox.list_folders()

        assert folders == ["INBOX", "Sent", "Drafts"]

    def test_folder_exists_true(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
            ((), b"/", "Receipts"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        assert mailbox.folder_exists("Receipts") is True

    def test_folder_exists_false(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        assert mailbox.folder_exists("NonExistent") is False

    def test_create_folder_new(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        result = mailbox.create_folder("NewFolder")

        assert result is True
        mock_imap_client.create_folder.assert_called_once_with("NewFolder")

    def test_create_folder_already_exists(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
            ((), b"/", "ExistingFolder"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        result = mailbox.create_folder("ExistingFolder")

        assert result is False
        mock_imap_client.create_folder.assert_not_called()

    def test_ensure_folder_creates_when_missing(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        mailbox.ensure_folder("NewFolder")

        mock_imap_client.create_folder.assert_called_once_with("NewFolder")

    def test_ensure_folder_skips_when_exists(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
            ((), b"/", "ExistingFolder"),
        ]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        mailbox.ensure_folder("ExistingFolder")

        mock_imap_client.create_folder.assert_not_called()


class TestImapMailboxAppend:
    def test_append_email_basic(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = mailbox.append_email("INBOX", raw_email)

        mock_imap_client.append.assert_called_once()
        call_args = mock_imap_client.append.call_args
        assert call_args[0][0] == "INBOX"
        assert call_args[0][1] == raw_email
        assert result is None  # No UIDPLUS response

    def test_append_email_with_flags(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        mailbox.append_email("INBOX", raw_email, flags=(r"\Seen", r"\Flagged"))

        call_args = mock_imap_client.append.call_args
        assert call_args[1]["flags"] == (r"\Seen", r"\Flagged")

    def test_append_email_with_uidplus_response(self, imap_config, mock_imap_client):
        # Server returns UIDPLUS response with new UID
        mock_imap_client.append.return_value = b"[APPENDUID 1234567890 42] APPEND completed"

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = mailbox.append_email("INBOX", raw_email)

        assert result == 42

    def test_append_email_with_timestamp(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        timestamp = 1700000000.0  # Nov 14, 2023
        mailbox.append_email("INBOX", raw_email, msg_time=timestamp)

        call_args = mock_imap_client.append.call_args
        assert call_args[1]["msg_time"] is not None


class TestImapMailboxOperations:
    def test_select_folder(self, imap_config, mock_imap_client):
        mock_imap_client.select_folder.return_value = {"EXISTS": 10}

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        result = mailbox.select_folder("INBOX")

        mock_imap_client.select_folder.assert_called_once_with("INBOX")
        assert result == {"EXISTS": 10}

    def test_move_email(self, imap_config, mock_imap_client):
        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        mailbox.move_email(123, "INBOX", "Archive")

        mock_imap_client.select_folder.assert_called_with("INBOX")
        mock_imap_client.move.assert_called_once_with([123], "Archive")

    def test_fetch_recent_uids(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1, 2, 3, 4, 5]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        uids = mailbox.fetch_recent_uids("INBOX", limit=3)

        assert uids == [3, 4, 5]

    def test_fetch_recent_uids_empty_folder(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = []

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        uids = mailbox.fetch_recent_uids("INBOX")

        assert uids == []

    def test_get_new_uids_since(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [101, 102, 103]

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        uids = mailbox.get_new_uids_since("INBOX", last_uid=100)

        assert uids == [101, 102, 103]

    def test_fetch_email(self, imap_config, mock_imap_client):
        raw_email = b"From: sender@example.com\r\nSubject: Test Email\r\nMessage-ID: <test123@example.com>\r\n\r\nEmail body"
        mock_imap_client.fetch.return_value = {
            123: {b"RFC822": raw_email}
        }

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        msg = mailbox.fetch_email(123, "INBOX")

        assert msg is not None
        assert msg.message_id == "<test123@example.com>"
        assert msg.subject == "Test Email"
        assert msg.from_addr == "sender@example.com"
        assert msg.uid == 123
        assert msg.folder == "INBOX"

    def test_fetch_email_not_found(self, imap_config, mock_imap_client):
        mock_imap_client.fetch.return_value = {}

        mailbox = ImapMailbox(imap_config)
        mailbox.connect()
        msg = mailbox.fetch_email(999, "INBOX")

        assert msg is None
