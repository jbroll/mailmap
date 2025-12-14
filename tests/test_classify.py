"""Tests for classify command helpers."""

from unittest.mock import patch

import pytest

from mailmap.commands.classify import _get_raw_bytes
from mailmap.email import UnifiedEmail


class TestGetRawBytes:
    """Tests for the _get_raw_bytes helper function."""

    @pytest.mark.asyncio
    async def test_returns_raw_bytes_if_already_set(self):
        """Test that pre-populated raw_bytes is returned directly."""
        email = UnifiedEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Test body",
            source_type="thunderbird",
            raw_bytes=b"pre-populated content",
        )

        result = await _get_raw_bytes(email)
        assert result == b"pre-populated content"

    @pytest.mark.asyncio
    async def test_loads_from_thunderbird_mbox(self):
        """Test that Thunderbird source loads raw bytes from mbox."""
        email = UnifiedEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Test body",
            source_type="thunderbird",
            source_ref="/path/to/mbox",
        )

        with patch("mailmap.commands.classify.get_raw_email") as mock_get_raw:
            mock_get_raw.return_value = b"loaded from mbox"
            result = await _get_raw_bytes(email)

            assert result == b"loaded from mbox"
            mock_get_raw.assert_called_once_with("/path/to/mbox", "<test@example.com>")

    @pytest.mark.asyncio
    async def test_returns_none_for_thunderbird_without_source_ref(self):
        """Test that Thunderbird source without source_ref returns None."""
        email = UnifiedEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Test body",
            source_type="thunderbird",
            source_ref=None,
        )

        result = await _get_raw_bytes(email)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_imap_source(self):
        """Test that IMAP source without raw_bytes returns None."""
        email = UnifiedEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Test body",
            source_type="imap",
            source_ref=12345,  # UID
        )

        result = await _get_raw_bytes(email)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_websocket_source(self):
        """Test that WebSocket source without raw_bytes returns None."""
        email = UnifiedEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            body_text="Test body",
            source_type="websocket",
        )

        result = await _get_raw_bytes(email)
        assert result is None
