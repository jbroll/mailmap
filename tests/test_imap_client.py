"""Tests for IMAP client module."""

import email
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from mailmap.config import ImapConfig
from mailmap.imap_client import (
    ImapClient,
    decode_mime_header,
    extract_attachments,
    extract_body,
)


@pytest.fixture
def imap_config():
    return ImapConfig(
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="testpass",
    )


@pytest.fixture
def mock_imap_client():
    """Mock the underlying IMAPClient used by imap_tool."""
    with patch("imap_tool.client.IMAPClient") as mock_class:
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
        encoded = "=?UTF-8?B?SGVsbG8gV29ybGQ=?="
        result = decode_mime_header(encoded)
        assert result == "Hello World"

    def test_decode_mixed_header(self):
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


class TestImapClientConnection:
    def test_connect(self, imap_config, mock_imap_client):
        client = ImapClient(imap_config)
        client.connect()

        assert client._client is not None
        mock_imap_client.login.assert_called_once_with(
            imap_config.username, imap_config.password
        )

    def test_disconnect(self, imap_config, mock_imap_client):
        client = ImapClient(imap_config)
        client.connect()
        client.disconnect()

        assert client._client is None
        mock_imap_client.logout.assert_called_once()

    def test_raw_property_raises_when_not_connected(self, imap_config):
        client = ImapClient(imap_config)
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = client.raw

    def test_raw_property_returns_client_when_connected(self, imap_config, mock_imap_client):
        client = ImapClient(imap_config)
        client.connect()
        assert client.raw is not None


class TestImapClientFolders:
    def test_list_folders(self, imap_config, mock_imap_client):
        mock_imap_client.list_folders.return_value = [
            ((), b"/", "INBOX"),
            ((), b"/", "Sent"),
            ((), b"/", "Drafts"),
        ]

        client = ImapClient(imap_config)
        client.connect()
        folders = client.list_folders()

        assert folders == ["INBOX", "Sent", "Drafts"]

    def test_folder_exists_true(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.return_value = {b"MESSAGES": 5}

        client = ImapClient(imap_config)
        client.connect()
        assert client.folder_exists("Receipts") is True

    def test_folder_exists_false(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.side_effect = Exception("No such folder")

        client = ImapClient(imap_config)
        client.connect()
        assert client.folder_exists("NonExistent") is False

    def test_create_folder_new(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.side_effect = Exception("No such folder")

        client = ImapClient(imap_config)
        client.connect()
        result = client.create_folder("NewFolder")

        assert result is True
        mock_imap_client.create_folder.assert_called_once_with("NewFolder")

    def test_create_folder_already_exists(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.return_value = {b"MESSAGES": 0}

        client = ImapClient(imap_config)
        client.connect()
        result = client.create_folder("ExistingFolder")

        assert result is False
        mock_imap_client.create_folder.assert_not_called()

    def test_ensure_folder_creates_when_missing(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.side_effect = Exception("No such folder")

        client = ImapClient(imap_config)
        client.connect()
        client.ensure_folder("NewFolder")

        mock_imap_client.create_folder.assert_called_once_with("NewFolder")

    def test_ensure_folder_skips_when_exists(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.return_value = {b"MESSAGES": 0}

        client = ImapClient(imap_config)
        client.connect()
        client.ensure_folder("ExistingFolder")

        mock_imap_client.create_folder.assert_not_called()


class TestImapClientAppend:
    def test_append_email_basic(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        client = ImapClient(imap_config)
        client.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = client.append_email("INBOX", raw_email)

        mock_imap_client.append.assert_called_once()
        call_args = mock_imap_client.append.call_args
        assert call_args[0][0] == "INBOX"
        assert call_args[0][1] == raw_email
        assert result is None  # No UIDPLUS response

    def test_append_email_with_flags(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        client = ImapClient(imap_config)
        client.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        client.append_email("INBOX", raw_email, flags=(r"\Seen", r"\Flagged"))

        call_args = mock_imap_client.append.call_args
        assert call_args[1]["flags"] == (r"\Seen", r"\Flagged")

    def test_append_email_with_uidplus_response(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"[APPENDUID 1234567890 42] APPEND completed"

        client = ImapClient(imap_config)
        client.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = client.append_email("INBOX", raw_email)

        assert result == 42

    def test_append_email_with_timestamp(self, imap_config, mock_imap_client):
        mock_imap_client.append.return_value = b"OK"

        client = ImapClient(imap_config)
        client.connect()

        raw_email = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        timestamp = 1700000000.0
        client.append_email("INBOX", raw_email, msg_time=timestamp)

        call_args = mock_imap_client.append.call_args
        assert call_args[1]["msg_time"] is not None


class TestImapClientOperations:
    def test_select_folder(self, imap_config, mock_imap_client):
        mock_imap_client.select_folder.return_value = {"EXISTS": 10}

        client = ImapClient(imap_config)
        client.connect()
        result = client.select_folder("INBOX")

        mock_imap_client.select_folder.assert_called_once_with("INBOX")
        assert result == {"EXISTS": 10}

    def test_move_email(self, imap_config, mock_imap_client):
        mock_imap_client.folder_status.return_value = {b"MESSAGES": 0}

        client = ImapClient(imap_config)
        client.connect()
        client.move_email(123, "INBOX", "Archive")

        mock_imap_client.select_folder.assert_called_with("INBOX")
        mock_imap_client.move.assert_called_once_with([123], "Archive")

    def test_fetch_uids(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1, 2, 3, 4, 5]

        client = ImapClient(imap_config)
        client.connect()
        uids = client.fetch_uids("INBOX", limit=3)

        assert uids == [3, 4, 5]

    def test_fetch_uids_empty_folder(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = []

        client = ImapClient(imap_config)
        client.connect()
        uids = client.fetch_uids("INBOX")

        assert uids == []

    def test_get_new_uids_since(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [101, 102, 103]

        client = ImapClient(imap_config)
        client.connect()
        uids = client.get_new_uids_since("INBOX", last_uid=100)

        assert uids == [101, 102, 103]

    def test_fetch_email(self, imap_config, mock_imap_client):
        raw_email = b"From: sender@example.com\r\nSubject: Test Email\r\nMessage-ID: <test123@example.com>\r\n\r\nEmail body"
        mock_imap_client.fetch.return_value = {
            123: {b"BODY[]": raw_email, b"FLAGS": ()}
        }

        client = ImapClient(imap_config)
        client.connect()
        msg = client.fetch_email(123, "INBOX")

        assert msg is not None
        assert msg["message_id"] == "<test123@example.com>"
        assert msg["subject"] == "Test Email"
        assert msg["from"] == "sender@example.com"
        assert msg["uid"] == 123
        assert msg["folder"] == "INBOX"

    def test_fetch_email_not_found(self, imap_config, mock_imap_client):
        mock_imap_client.fetch.return_value = {}

        client = ImapClient(imap_config)
        client.connect()
        msg = client.fetch_email(999, "INBOX")

        assert msg is None

    def test_fetch_raw(self, imap_config, mock_imap_client):
        raw_email = b"From: sender@example.com\r\nSubject: Test Email\r\n\r\nBody"
        mock_imap_client.fetch.return_value = {
            123: {b"BODY[]": raw_email}
        }

        client = ImapClient(imap_config)
        client.connect()
        result = client.fetch_raw(123, "INBOX")

        assert result == raw_email
        mock_imap_client.select_folder.assert_called_with("INBOX")

    def test_fetch_raw_not_found(self, imap_config, mock_imap_client):
        mock_imap_client.fetch.return_value = {}

        client = ImapClient(imap_config)
        client.connect()
        result = client.fetch_raw(999, "INBOX")

        assert result is None


class TestFetchAllMessageIds:
    """Tests for fetch_all_message_ids with various header formats."""

    def test_simple_message_id(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1]
        mock_imap_client.fetch.return_value = {
            1: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <simple@example.com>\r\n"}
        }

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert ids == ["<simple@example.com>"]

    def test_folded_message_id_crlf(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1]
        mock_imap_client.fetch.return_value = {
            1: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID:\r\n <folded@example.com>\r\n"}
        }

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert ids == ["<folded@example.com>"]

    def test_folded_message_id_lf(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1]
        mock_imap_client.fetch.return_value = {
            1: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID:\n <folded@example.com>\n"}
        }

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert ids == ["<folded@example.com>"]

    def test_folded_message_id_tab(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1]
        mock_imap_client.fetch.return_value = {
            1: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID:\r\n\t<tabbed@example.com>\r\n"}
        }

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert ids == ["<tabbed@example.com>"]

    def test_multiple_message_ids(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = [1, 2, 3]
        mock_imap_client.fetch.return_value = {
            1: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID: <simple@example.com>\r\n"},
            2: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID:\r\n <folded@example.com>\r\n"},
            3: {b"BODY[HEADER.FIELDS (MESSAGE-ID)]": b"Message-ID:\n\t<tabbed@example.com>\n"},
        }

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert len(ids) == 3
        assert "<simple@example.com>" in ids
        assert "<folded@example.com>" in ids
        assert "<tabbed@example.com>" in ids

    def test_empty_folder(self, imap_config, mock_imap_client):
        mock_imap_client.search.return_value = []

        client = ImapClient(imap_config)
        client.connect()
        ids = client.fetch_all_message_ids("INBOX")

        assert ids == []


class TestExtractAttachments:
    """Tests for extract_attachments function."""

    def test_no_attachments_simple_message(self):
        raw = b"Content-Type: text/plain\r\n\r\nSimple body"
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert result == []

    def test_multipart_no_attachments(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain

Plain text body
--boundary
Content-Type: text/html

<html><body>HTML body</body></html>
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert result == []

    def test_text_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body text
--boundary
Content-Type: text/plain; name="notes.txt"
Content-Disposition: attachment; filename="notes.txt"

These are my notes.
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "notes.txt"
        assert result[0]["content_type"] == "text/plain"
        assert "These are my notes" in result[0]["text_content"]

    def test_ics_calendar_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

You have a new booking
--boundary
Content-Type: text/calendar; name="schedule.ics"
Content-Disposition: attachment; filename="schedule.ics"

BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Yoga Class with John
LOCATION:Studio A
DTSTART:20241215T100000Z
ORGANIZER:mailto:yoga@example.com
END:VEVENT
END:VCALENDAR
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "schedule.ics"
        assert result[0]["content_type"] == "text/calendar"
        text = result[0]["text_content"]
        assert "SUMMARY: Yoga Class with John" in text
        assert "LOCATION: Studio A" in text

    def test_ics_without_filename_defaults_to_calendar(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body
--boundary
Content-Type: text/calendar

BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Meeting
END:VEVENT
END:VCALENDAR
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "calendar.ics"

    def test_binary_attachment_no_text_content(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body
--boundary
Content-Type: application/pdf; name="document.pdf"
Content-Disposition: attachment; filename="document.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "document.pdf"
        assert result[0]["content_type"] == "application/pdf"
        assert result[0]["text_content"] is None

    def test_multiple_attachments(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Body
--boundary
Content-Type: text/plain; name="notes.txt"
Content-Disposition: attachment; filename="notes.txt"

Note content
--boundary
Content-Type: application/pdf; name="doc.pdf"
Content-Disposition: attachment; filename="doc.pdf"

binary
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 2
        filenames = [a["filename"] for a in result]
        assert "notes.txt" in filenames
        assert "doc.pdf" in filenames

    def test_csv_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Order details attached
--boundary
Content-Type: text/csv; name="order.csv"
Content-Disposition: attachment; filename="order.csv"

Product,Quantity,Price
Widget A,5,19.99
Widget B,2,29.99
Widget C,1,49.99
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "order.csv"
        text = result[0]["text_content"]
        assert "Columns: Product, Quantity, Price" in text
        assert "Rows: 3" in text

    def test_json_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Receipt attached
--boundary
Content-Type: application/json; name="receipt.json"
Content-Disposition: attachment; filename="receipt.json"

{"order_id": "12345", "total": 99.99, "items": [{"name": "Widget", "qty": 2}]}
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "receipt.json"
        text = result[0]["text_content"]
        assert "order_id" in text
        assert "12345" in text

    def test_xml_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Invoice attached
--boundary
Content-Type: application/xml; name="invoice.xml"
Content-Disposition: attachment; filename="invoice.xml"

<?xml version="1.0"?>
<invoice>
  <id>INV-001</id>
  <amount>150.00</amount>
  <status>paid</status>
</invoice>
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "invoice.xml"
        text = result[0]["text_content"]
        assert "Root: <invoice>" in text

    def test_application_ics_attachment(self):
        raw = b"""MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary"

--boundary
Content-Type: text/plain

Meeting invite
--boundary
Content-Type: application/ics; name="meeting.ics"
Content-Disposition: attachment; filename="meeting.ics"

BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Team Meeting
LOCATION:Room 101
END:VEVENT
END:VCALENDAR
--boundary--"""
        msg = email.message_from_bytes(raw)
        result = extract_attachments(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "meeting.ics"
        text = result[0]["text_content"]
        assert "SUMMARY: Team Meeting" in text
        assert "LOCATION: Room 101" in text
