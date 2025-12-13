"""Tests for email source abstractions."""

import pytest

from mailmap.config import Config, ImapConfig, ThunderbirdConfig
from mailmap.email import UnifiedEmail
from mailmap.sources import (
    ImapSource,
    ThunderbirdSource,
    select_source,
)
from mailmap.sources.base import EmailSource as EmailSourceProtocol
from mailmap.thunderbird import ThunderbirdEmail


class TestUnifiedEmail:
    def test_from_thunderbird(self):
        tb_email = ThunderbirdEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="Test body",
            mbox_path="/path/to/mbox",
            headers={"X-Spam-Flag": "NO"},
        )

        email = UnifiedEmail.from_thunderbird(tb_email)

        assert email.message_id == "<test@example.com>"
        assert email.folder == "INBOX"
        assert email.subject == "Test Subject"
        assert email.from_addr == "sender@example.com"
        assert email.body_text == "Test body"
        assert email.source_type == "thunderbird"
        assert email.source_ref == "/path/to/mbox"
        assert email.headers == {"X-Spam-Flag": "NO"}

    def test_from_thunderbird_no_headers(self):
        tb_email = ThunderbirdEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Body",
            mbox_path="/path",
            headers=None,
        )

        email = UnifiedEmail.from_thunderbird(tb_email)
        assert email.headers == {}

    def test_from_imap(self):
        email = UnifiedEmail.from_imap(
            message_id="<imap@example.com>",
            folder="INBOX",
            subject="IMAP Subject",
            from_addr="imap@example.com",
            body_text="IMAP body",
            uid=12345,
            headers={"From": "imap@example.com"},
        )

        assert email.message_id == "<imap@example.com>"
        assert email.folder == "INBOX"
        assert email.source_type == "imap"
        assert email.source_ref == 12345
        assert email.headers == {"From": "imap@example.com"}

    def test_from_websocket(self):
        email = UnifiedEmail.from_websocket(
            message_id="<ws@example.com>",
            folder="Sent",
            subject="WS Subject",
            from_addr="ws@example.com",
            body_text="WS body",
        )

        assert email.message_id == "<ws@example.com>"
        assert email.source_type == "websocket"
        assert email.source_ref is None


class TestThunderbirdSource:
    def test_source_type(self):
        source = ThunderbirdSource()
        assert source.source_type == "thunderbird"

    @pytest.mark.asyncio
    async def test_connect_without_profile_raises(self, temp_dir):
        source = ThunderbirdSource(profile_path=temp_dir / "nonexistent")
        with pytest.raises(ValueError, match="Could not find Thunderbird profile"):
            await source.connect()

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_thunderbird_profile):
        source = ThunderbirdSource(profile_path=mock_thunderbird_profile)
        async with source as s:
            assert s._reader is not None
        assert s._reader is None

    @pytest.mark.asyncio
    async def test_list_folders(self, mock_thunderbird_profile):
        source = ThunderbirdSource(profile_path=mock_thunderbird_profile)
        async with source:
            folders = await source.list_folders()
            # Should be qualified (server:folder) format
            assert any("INBOX" in f for f in folders)

    @pytest.mark.asyncio
    async def test_read_emails(self, mock_thunderbird_profile):
        source = ThunderbirdSource(profile_path=mock_thunderbird_profile)
        async with source:
            folders = await source.list_folders()
            inbox = [f for f in folders if "INBOX" in f][0]

            emails = []
            async for email in source.read_emails(inbox):
                emails.append(email)

            assert len(emails) == 2
            assert all(isinstance(e, UnifiedEmail) for e in emails)
            assert all(e.source_type == "thunderbird" for e in emails)

    @pytest.mark.asyncio
    async def test_read_emails_with_limit(self, mock_thunderbird_profile):
        source = ThunderbirdSource(profile_path=mock_thunderbird_profile)
        async with source:
            folders = await source.list_folders()
            inbox = [f for f in folders if "INBOX" in f][0]

            emails = []
            async for email in source.read_emails(inbox, limit=1):
                emails.append(email)

            assert len(emails) == 1


class TestImapSource:
    def test_source_type(self):
        config = ImapConfig(host="imap.example.com")
        source = ImapSource(config)
        assert source.source_type == "imap"


class TestSelectSource:
    def test_select_thunderbird_when_available(self, mock_thunderbird_profile):
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            thunderbird=ThunderbirdConfig(profile_path=str(mock_thunderbird_profile)),
        )
        source = select_source(config)
        assert isinstance(source, ThunderbirdSource)
        assert source.source_type == "thunderbird"

    def test_select_imap_when_no_thunderbird(self, temp_dir):
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            thunderbird=ThunderbirdConfig(profile_path=str(temp_dir / "nonexistent")),
        )
        source = select_source(config)
        assert isinstance(source, ImapSource)
        assert source.source_type == "imap"

    def test_raises_when_no_source_available(self, temp_dir):
        config = Config(
            imap=ImapConfig(host=""),  # Empty host
            thunderbird=ThunderbirdConfig(profile_path=str(temp_dir / "nonexistent")),
        )
        with pytest.raises(ValueError, match="No email source available"):
            select_source(config)


class TestEmailSourceProtocol:
    def test_thunderbird_source_implements_protocol(self):
        source = ThunderbirdSource()
        assert isinstance(source, EmailSourceProtocol)

    def test_imap_source_implements_protocol(self):
        config = ImapConfig(host="imap.example.com")
        source = ImapSource(config)
        assert isinstance(source, EmailSourceProtocol)
