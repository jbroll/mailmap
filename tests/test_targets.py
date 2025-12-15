"""Tests for email target abstractions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailmap.config import Config, DatabaseConfig, ImapConfig, ThunderbirdConfig, WebSocketConfig
from mailmap.targets import (
    ImapTarget,
    WebSocketTarget,
    select_target,
)
from mailmap.targets.base import EmailTarget as EmailTargetProtocol


@pytest.fixture
def mock_config():
    """Create a mock config for WebSocketTarget tests."""
    return Config(
        imap=ImapConfig(host="imap.example.com"),
        websocket=WebSocketConfig(enabled=True, auth_token="test-token"),
        thunderbird=ThunderbirdConfig(),
        database=DatabaseConfig(path="test.db", categories_file="categories.txt"),
    )


class TestWebSocketTarget:
    def test_target_type(self, mock_config):
        target = WebSocketTarget(mock_config, "local", 9753)
        assert target.target_type == "websocket"

    @pytest.mark.asyncio
    async def test_connect_raises_on_timeout(self, mock_config):
        """Test that connect raises when no extension connects."""
        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = None  # Timeout

            target = WebSocketTarget(mock_config, "local", 9753)
            with pytest.raises(RuntimeError, match="Timeout waiting for Thunderbird extension"):
                await target.connect()

    @pytest.mark.asyncio
    async def test_connect_resolves_local_account(self, mock_config):
        """Test that connect resolves 'local' to Local Folders account ID."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(return_value=MagicMock(
            ok=True,
            result={"accounts": [{"id": "account1", "type": "none"}]}
        ))
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            target = WebSocketTarget(mock_config, "local", 9753)
            await target.connect()

            assert target._account_id == "account1"

            await target.disconnect()

    @pytest.mark.asyncio
    async def test_operations_fail_when_not_connected(self, mock_config):
        target = WebSocketTarget(mock_config, "local", 9753)
        # Not connected

        with pytest.raises(RuntimeError, match="Target not connected"):
            await target.create_folder("Test")

    @pytest.mark.asyncio
    async def test_create_folder(self, mock_config):
        """Test creating a folder via WebSocket."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={"created": True}),
        ])
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            async with WebSocketTarget(mock_config, "local", 9753) as target:
                result = await target.create_folder("TestFolder")
                assert result is True

    @pytest.mark.asyncio
    async def test_copy_email(self, mock_config):
        """Test copying an email via WebSocket."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            async with WebSocketTarget(mock_config, "local", 9753) as target:
                result = await target.copy_email("<msg@example.com>", "Inbox")
                assert result is True

    @pytest.mark.asyncio
    async def test_move_email(self, mock_config):
        """Test moving an email via WebSocket."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            async with WebSocketTarget(mock_config, "local", 9753) as target:
                result = await target.move_email("<msg@example.com>", "Archive")
                assert result is True


class TestImapTarget:
    def test_target_type(self):
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)
        assert target.target_type == "imap"


class TestSelectTarget:
    def test_select_imap_target_explicitly(self):
        """Test that target_account='imap' selects ImapTarget."""
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
        )
        target = select_target(config, "imap")
        assert isinstance(target, ImapTarget)

    def test_select_websocket_for_local_with_port(self, mock_config):
        """Test that 'local' with websocket_port selects WebSocketTarget."""
        target = select_target(mock_config, "local", websocket_port=9753)
        assert isinstance(target, WebSocketTarget)

    def test_raises_for_local_without_websocket_port(self):
        """Test that 'local' without websocket_port raises an error."""
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
        )
        with pytest.raises(ValueError, match="requires --websocket"):
            select_target(config, "local")

    def test_falls_back_to_imap_without_websocket_port(self):
        """Test that server names fall back to IMAP when no websocket_port."""
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
        )
        target = select_target(config, "outlook.office365.com")
        assert isinstance(target, ImapTarget)

    def test_select_websocket_for_server_with_port(self, mock_config):
        """Test that server name with websocket_port selects WebSocketTarget."""
        target = select_target(mock_config, "imap.example.com", websocket_port=9753)
        assert isinstance(target, WebSocketTarget)


class TestWebSocketTargetWithRawBytes:
    """Test that WebSocketTarget accepts but ignores raw_bytes."""

    @pytest.mark.asyncio
    async def test_copy_email_with_raw_bytes(self, mock_config):
        """Test copy_email accepts raw_bytes parameter."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            async with WebSocketTarget(mock_config, "local", 9753) as target:
                # raw_bytes should be accepted but ignored
                result = await target.copy_email("<msg@example.com>", "Inbox", raw_bytes=b"raw email")
                assert result is True

    @pytest.mark.asyncio
    async def test_move_email_with_raw_bytes(self, mock_config):
        """Test move_email accepts raw_bytes parameter."""
        mock_ws_server = MagicMock()
        mock_ws_server.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])
        mock_ws_server.stop = AsyncMock()
        mock_task = MagicMock()

        with patch("mailmap.websocket_server.start_websocket_and_wait", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = (mock_ws_server, mock_task)

            async with WebSocketTarget(mock_config, "local", 9753) as target:
                # raw_bytes should be accepted but ignored
                result = await target.move_email("<msg@example.com>", "Archive", raw_bytes=b"raw email")
                assert result is True


class TestImapTargetWithRawBytes:
    """Test ImapTarget copy/move with raw_bytes for cross-server transfers."""

    @pytest.mark.asyncio
    async def test_copy_email_with_raw_bytes_uploads_directly(self):
        """Test that copy_email uploads raw_bytes directly without searching."""
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)

        # Mock the mailbox
        mock_mailbox = MagicMock()
        mock_mailbox.ensure_folder = MagicMock()
        mock_mailbox.append_email = MagicMock()
        target._mailbox = mock_mailbox

        raw_content = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = await target.copy_email("<msg@example.com>", "Inbox", raw_bytes=raw_content)

        assert result is True
        mock_mailbox.ensure_folder.assert_called_once_with("Inbox")
        mock_mailbox.append_email.assert_called_once_with("Inbox", raw_content)

    @pytest.mark.asyncio
    async def test_move_email_with_raw_bytes_uploads_directly(self):
        """Test that move_email uploads raw_bytes directly without searching."""
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)

        # Mock the mailbox
        mock_mailbox = MagicMock()
        mock_mailbox.ensure_folder = MagicMock()
        mock_mailbox.append_email = MagicMock()
        target._mailbox = mock_mailbox

        raw_content = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
        result = await target.move_email("<msg@example.com>", "Archive", raw_bytes=raw_content)

        assert result is True
        mock_mailbox.ensure_folder.assert_called_once_with("Archive")
        mock_mailbox.append_email.assert_called_once_with("Archive", raw_content)


class TestImapTargetDuplicatePrevention:
    """Test that ImapTarget prevents duplicate copies."""

    @pytest.mark.asyncio
    async def test_copy_email_skips_if_already_in_target_folder(self):
        """Test that copy_email returns True without appending if email is already in target."""
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)

        # Mock the mailbox
        mock_mailbox = MagicMock()
        mock_mailbox.ensure_folder = MagicMock()
        mock_mailbox.list_folders = MagicMock(return_value=["INBOX", "Personal"])
        mock_mailbox.select_folder = MagicMock()
        # Email is NOT in INBOX (empty search), but IS in Personal (the target)
        mock_mailbox.client.search = MagicMock(side_effect=[
            [],     # Not in INBOX
            [123],  # Found in Personal (target folder)
        ])
        mock_mailbox.append_email = MagicMock()
        target._mailbox = mock_mailbox

        # Try to copy to "Personal" when email is already there
        result = await target.copy_email("<msg@example.com>", "Personal", raw_bytes=None)

        assert result is True
        # append_email should NOT be called since email is already in target
        mock_mailbox.append_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_copy_email_copies_if_in_different_folder(self):
        """Test that copy_email copies when email is in a different folder."""
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)

        # Mock the mailbox
        mock_mailbox = MagicMock()
        mock_mailbox.ensure_folder = MagicMock()
        mock_mailbox.list_folders = MagicMock(return_value=["INBOX", "Personal"])
        mock_mailbox.select_folder = MagicMock()
        # Email is in INBOX, not Personal
        mock_mailbox.client.search = MagicMock(side_effect=[
            [123],  # Found in INBOX
        ])
        mock_mailbox.client.fetch = MagicMock(return_value={
            123: {b"BODY[]": b"raw email content"}
        })
        mock_mailbox.append_email = MagicMock()
        target._mailbox = mock_mailbox

        result = await target.copy_email("<msg@example.com>", "Personal", raw_bytes=None)

        assert result is True
        mock_mailbox.append_email.assert_called_once_with("Personal", b"raw email content")


class TestEmailTargetProtocol:
    def test_websocket_target_implements_protocol(self, mock_config):
        target = WebSocketTarget(mock_config, "local", 9753)
        assert isinstance(target, EmailTargetProtocol)

    def test_imap_target_implements_protocol(self):
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)
        assert isinstance(target, EmailTargetProtocol)
