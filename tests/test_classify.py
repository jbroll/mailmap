"""Tests for classify command helpers."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailmap.commands.classify import (
    ProcessingStats,
    _get_raw_bytes,
    _transfer_single_email,
)
from mailmap.database import Email
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


class TestTransferSingleEmail:
    """Tests for the _transfer_single_email helper function."""

    @pytest.fixture
    def email_record(self):
        """Create a test email record."""
        return Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            mbox_path="/path/to/mbox",
            classification="Work",
            confidence=0.95,
            processed_at=datetime.now(),
        )

    @pytest.fixture
    def mock_target(self):
        """Create a mock target."""
        target = AsyncMock()
        target.copy_email = AsyncMock(return_value=True)
        target.move_email = AsyncMock(return_value=True)
        return target

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.mark_as_transferred = MagicMock()
        return db

    @pytest.mark.asyncio
    async def test_successful_copy(self, email_record, mock_target, mock_db):
        """Test successful copy transfer."""
        stats = ProcessingStats()

        result = await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=False,
            stats=stats,
            rate_limit=0.0,  # No delay for tests
        )

        assert result is True
        assert stats.copied == 1
        assert stats.failed == 0
        mock_target.copy_email.assert_called_once_with(
            "<test@example.com>", "Work", None
        )
        mock_db.mark_as_transferred.assert_called_once_with("<test@example.com>")

    @pytest.mark.asyncio
    async def test_successful_move(self, email_record, mock_target, mock_db):
        """Test successful move transfer."""
        stats = ProcessingStats()

        result = await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=True,
            stats=stats,
            rate_limit=0.0,
        )

        assert result is True
        assert stats.copied == 1
        mock_target.move_email.assert_called_once_with(
            "<test@example.com>", "Work", None
        )

    @pytest.mark.asyncio
    async def test_failed_transfer(self, email_record, mock_target, mock_db):
        """Test failed transfer increments failed count."""
        mock_target.copy_email = AsyncMock(return_value=False)
        stats = ProcessingStats()

        result = await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=False,
            stats=stats,
            rate_limit=0.0,
        )

        assert result is False
        assert stats.failed == 1
        assert stats.copied == 0
        mock_db.mark_as_transferred.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_handling(self, email_record, mock_target, mock_db):
        """Test exception during transfer increments failed count."""
        mock_target.copy_email = AsyncMock(side_effect=Exception("Connection error"))
        stats = ProcessingStats()

        result = await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=False,
            stats=stats,
            rate_limit=0.0,
        )

        assert result is False
        assert stats.failed == 1

    @pytest.mark.asyncio
    async def test_uses_unknown_folder_when_no_classification(
        self, mock_target, mock_db
    ):
        """Test that Unknown folder is used when classification is None."""
        email_record = Email(
            message_id="<test@example.com>",
            folder_id="INBOX",
            subject="Test",
            from_addr="sender@example.com",
            mbox_path="",
            classification=None,
            processed_at=datetime.now(),
        )
        stats = ProcessingStats()

        await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=False,
            stats=stats,
            rate_limit=0.0,
        )

        mock_target.copy_email.assert_called_once_with(
            "<test@example.com>", "Unknown", None
        )

    @pytest.mark.asyncio
    async def test_rate_limiting(self, email_record, mock_target, mock_db):
        """Test that rate limiting adds delay."""
        stats = ProcessingStats()

        start_time = asyncio.get_event_loop().time()
        await _transfer_single_email(
            email_record=email_record,
            target=mock_target,
            db=mock_db,
            move=False,
            stats=stats,
            rate_limit=0.1,  # 100ms rate limit
        )
        elapsed = asyncio.get_event_loop().time() - start_time

        # Should take at least 100ms due to rate limiting
        assert elapsed >= 0.1


class TestProcessingStats:
    """Tests for ProcessingStats dataclass."""

    @pytest.mark.asyncio
    async def test_increment_single_field(self):
        """Test incrementing a single field."""
        stats = ProcessingStats()
        await stats.increment(classified=1)
        assert stats.classified == 1

    @pytest.mark.asyncio
    async def test_increment_multiple_fields(self):
        """Test incrementing multiple fields at once."""
        stats = ProcessingStats()
        await stats.increment(imported=5, classified=3, failed=1)
        assert stats.imported == 5
        assert stats.classified == 3
        assert stats.failed == 1

    @pytest.mark.asyncio
    async def test_concurrent_increments(self):
        """Test that concurrent increments are thread-safe."""
        stats = ProcessingStats()

        async def increment_many():
            for _ in range(100):
                await stats.increment(classified=1)

        # Run 10 concurrent tasks each incrementing 100 times
        await asyncio.gather(*[increment_many() for _ in range(10)])

        assert stats.classified == 1000
