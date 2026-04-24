"""Tests for IMAP reconnection logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailmap.commands.daemon import EmailProcessor
from mailmap.config import Config, DatabaseConfig, ImapConfig, OllamaConfig
from mailmap.imap_client import ImapListener


@pytest.fixture
def imap_config():
    return ImapConfig(
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="password",
        idle_folders=["INBOX"],
    )


@pytest.fixture
def config(imap_config):
    return Config(
        imap=imap_config,
        ollama=OllamaConfig(),
        database=DatabaseConfig(),
    )


class TestImapListenerReconnection:
    """Test ImapListener reconnection behavior."""

    def test_calculate_backoff(self, imap_config):
        listener = ImapListener(imap_config)

        assert listener._calculate_backoff(0) == 5
        assert listener._calculate_backoff(1) == 10
        assert listener._calculate_backoff(2) == 20
        assert listener._calculate_backoff(3) == 40
        assert listener._calculate_backoff(10) == 300  # Capped at MAX_RETRY_DELAY

    @pytest.mark.asyncio
    async def test_watch_folder_idle_reconnects_on_failure(self, imap_config):
        listener = ImapListener(imap_config)
        listener._running = True
        callback = MagicMock()
        attempts = []

        async def mock_run_idle_loop(client, folder, cb):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")
            listener._running = False

        with (
            patch.object(listener, '_run_idle_loop', side_effect=mock_run_idle_loop),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            await listener.watch_folder_idle("INBOX", callback)

        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_poll_folder_reconnects_on_failure(self, imap_config):
        listener = ImapListener(imap_config)
        listener._running = True
        callback = MagicMock()
        attempts = []

        async def mock_check_folder_once(folder):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")
            listener._running = False
            return []

        with (
            patch.object(listener, '_check_folder_once', side_effect=mock_check_folder_once),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            await listener.poll_folder("INBOX", callback, interval=1)

        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_watch_folder_stops_when_not_running(self, imap_config):
        listener = ImapListener(imap_config)
        listener._running = False
        callback = MagicMock()
        await listener.watch_folder_idle("INBOX", callback)


class TestEmailProcessorReconnection:
    """Test EmailProcessor reconnection behavior."""

    def test_reconnect_client(self, config):
        """Test that _reconnect_client creates a new connection."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        mock_client = MagicMock()
        processor._client = mock_client

        with patch('mailmap.commands.daemon.ImapClient') as MockClient:
            new_client = MagicMock()
            MockClient.return_value = new_client

            result = processor._reconnect_client()

            mock_client.disconnect.assert_called_once()
            new_client.connect.assert_called_once()
            assert result == new_client
            assert processor._client == new_client

    def test_move_to_folder_retries_on_failure(self, config):
        """Test that _move_to_folder retries on connection failure."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = {"uid": 123, "folder": "INBOX"}
        attempts = []

        def mock_move_email(uid, src, dest):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("Connection lost")

        mock_client = MagicMock()
        mock_client.move_email = mock_move_email

        with (
            patch.object(processor, '_get_client', return_value=mock_client),
            patch.object(processor, '_reconnect_client', return_value=mock_client),
            patch('time.sleep'),
        ):
            processor._move_to_folder(message, "Archive")

        assert len(attempts) == 3

    def test_move_to_folder_gives_up_after_max_retries(self, config):
        """Test that _move_to_folder gives up after MAX_MOVE_RETRIES."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = {"uid": 123, "folder": "INBOX"}

        mock_client = MagicMock()
        mock_client.move_email.side_effect = ConnectionError("Connection lost")

        with (
            patch.object(processor, '_get_client', return_value=mock_client),
            patch.object(processor, '_reconnect_client', return_value=mock_client),
            patch('time.sleep'),
        ):
            processor._move_to_folder(message, "Archive")

        assert mock_client.move_email.call_count == processor.MAX_MOVE_RETRIES

    def test_move_to_folder_succeeds_first_try(self, config):
        """Test that _move_to_folder works on first try."""
        db = MagicMock()
        processor = EmailProcessor(config, db, move=True)

        message = {"uid": 123, "folder": "INBOX"}
        mock_client = MagicMock()

        with patch.object(processor, '_get_client', return_value=mock_client):
            processor._move_to_folder(message, "Archive")

        mock_client.ensure_folder.assert_called_once_with("Archive")
        mock_client.move_email.assert_called_once_with(123, "INBOX", "Archive")
