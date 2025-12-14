"""Tests for IMAP reconnection logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailmap.commands.daemon import EmailProcessor
from mailmap.config import Config, DatabaseConfig, ImapConfig, OllamaConfig
from mailmap.imap_client import ImapListener


@pytest.fixture
def imap_config():
    """Create a test IMAP configuration."""
    return ImapConfig(
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="password",
        idle_folders=["INBOX"],
    )


@pytest.fixture
def config(imap_config):
    """Create a full test configuration."""
    return Config(
        imap=imap_config,
        ollama=OllamaConfig(),
        database=DatabaseConfig(),
    )


class TestImapListenerReconnection:
    """Test ImapListener reconnection behavior."""

    def test_calculate_backoff(self, imap_config):
        """Test exponential backoff calculation."""
        listener = ImapListener(imap_config)

        assert listener._calculate_backoff(0) == 5  # Initial delay
        assert listener._calculate_backoff(1) == 10  # 5 * 2
        assert listener._calculate_backoff(2) == 20  # 5 * 4
        assert listener._calculate_backoff(3) == 40  # 5 * 8
        assert listener._calculate_backoff(10) == 300  # Capped at MAX_RETRY_DELAY

    @pytest.mark.asyncio
    async def test_watch_folder_idle_reconnects_on_failure(self, imap_config):
        """Test that watch_folder_idle reconnects after connection failure."""
        listener = ImapListener(imap_config)
        listener._running = True
        callback = MagicMock()
        attempts = []

        async def mock_run_idle_loop(mailbox, folder, cb):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")
            # Third attempt succeeds, then we stop
            listener._running = False

        with (
            patch.object(listener, '_run_idle_loop', side_effect=mock_run_idle_loop),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            await listener.watch_folder_idle("INBOX", callback)

        assert len(attempts) == 3  # Failed twice, succeeded once

    @pytest.mark.asyncio
    async def test_poll_folder_reconnects_on_failure(self, imap_config):
        """Test that poll_folder reconnects after connection failure."""
        listener = ImapListener(imap_config)
        listener._running = True
        callback = MagicMock()
        attempts = []

        async def mock_check_folder_once(folder):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")
            # Third attempt succeeds, then we stop
            listener._running = False
            return []

        with (
            patch.object(listener, '_check_folder_once', side_effect=mock_check_folder_once),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            await listener.poll_folder("INBOX", callback, interval=1)

        assert len(attempts) == 3  # Failed twice, succeeded once

    @pytest.mark.asyncio
    async def test_watch_folder_stops_when_not_running(self, imap_config):
        """Test that watch_folder_idle exits cleanly when stopped."""
        listener = ImapListener(imap_config)
        listener._running = False  # Already stopped

        callback = MagicMock()

        # Should exit immediately without attempting connection
        await listener.watch_folder_idle("INBOX", callback)
        # No assertion needed - just verify it doesn't hang


class TestEmailProcessorReconnection:
    """Test EmailProcessor reconnection behavior."""

    def test_reconnect_mailbox(self, config):
        """Test that _reconnect_mailbox creates a new connection."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        # Create initial mock mailbox
        mock_mailbox = MagicMock()
        processor._mailbox = mock_mailbox

        with patch('mailmap.commands.daemon.ImapMailbox') as MockMailbox:
            new_mailbox = MagicMock()
            MockMailbox.return_value = new_mailbox

            result = processor._reconnect_mailbox()

            mock_mailbox.disconnect.assert_called_once()
            new_mailbox.connect.assert_called_once()
            assert result == new_mailbox
            assert processor._mailbox == new_mailbox

    def test_move_to_folder_retries_on_failure(self, config):
        """Test that _move_to_folder retries on connection failure."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = MagicMock()
        message.uid = 123
        message.folder = "INBOX"

        attempts = []

        def mock_move_email(uid, src, dest):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")
            # Third attempt succeeds

        mock_mailbox = MagicMock()
        mock_mailbox.move_email = mock_move_email

        with (
            patch.object(processor, '_get_mailbox', return_value=mock_mailbox),
            patch.object(processor, '_reconnect_mailbox', return_value=mock_mailbox),
            patch('time.sleep'),
        ):
            processor._move_to_folder(message, "Archive")

        assert len(attempts) == 3  # Retried until success

    def test_move_to_folder_gives_up_after_max_retries(self, config):
        """Test that _move_to_folder gives up after MAX_MOVE_RETRIES."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = MagicMock()
        message.uid = 123
        message.folder = "INBOX"

        mock_mailbox = MagicMock()
        mock_mailbox.move_email.side_effect = ConnectionError("Connection lost")

        with (
            patch.object(processor, '_get_mailbox', return_value=mock_mailbox),
            patch.object(processor, '_reconnect_mailbox', return_value=mock_mailbox),
            patch('time.sleep'),
        ):
            processor._move_to_folder(message, "Archive")

        # Should have tried MAX_MOVE_RETRIES times
        assert mock_mailbox.move_email.call_count == processor.MAX_MOVE_RETRIES

    def test_move_to_folder_succeeds_first_try(self, config):
        """Test that _move_to_folder works on first try."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = MagicMock()
        message.uid = 123
        message.folder = "INBOX"

        mock_mailbox = MagicMock()

        with patch.object(processor, '_get_mailbox', return_value=mock_mailbox):
            processor._move_to_folder(message, "Archive")

        mock_mailbox.ensure_folder.assert_called_once_with("Archive")
        mock_mailbox.move_email.assert_called_once_with(123, "INBOX", "Archive")
