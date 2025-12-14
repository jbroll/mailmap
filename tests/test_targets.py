"""Tests for email target abstractions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mailmap.config import Config, ImapConfig, ThunderbirdConfig, WebSocketConfig
from mailmap.targets import (
    ImapTarget,
    WebSocketTarget,
    select_target,
)
from mailmap.targets.base import EmailTarget as EmailTargetProtocol


class TestWebSocketTarget:
    def test_target_type(self):
        mock_ws = MagicMock()
        target = WebSocketTarget(mock_ws, "local")
        assert target.target_type == "websocket"

    @pytest.mark.asyncio
    async def test_connect_raises_when_not_connected(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = False

        target = WebSocketTarget(mock_ws, "local")
        with pytest.raises(RuntimeError, match="No Thunderbird extension connected"):
            await target.connect()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(return_value=MagicMock(
            ok=True,
            result={"accounts": [{"id": "account1", "type": "none"}]}
        ))

        async with WebSocketTarget(mock_ws, "local") as target:
            assert target._account_id == "account1"
        assert target._account_id is None

    @pytest.mark.asyncio
    async def test_create_folder(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={"created": True}),
        ])

        async with WebSocketTarget(mock_ws, "local") as target:
            result = await target.create_folder("TestFolder")
            assert result is True

    @pytest.mark.asyncio
    async def test_copy_email(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])

        async with WebSocketTarget(mock_ws, "local") as target:
            result = await target.copy_email("<msg@example.com>", "Inbox")
            assert result is True

    @pytest.mark.asyncio
    async def test_move_email(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])

        async with WebSocketTarget(mock_ws, "local") as target:
            result = await target.move_email("<msg@example.com>", "Archive")
            assert result is True

    @pytest.mark.asyncio
    async def test_operations_fail_when_not_connected(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(return_value=MagicMock(
            ok=True,
            result={"accounts": [{"id": "acc1", "type": "none"}]}
        ))

        target = WebSocketTarget(mock_ws, "local")
        # Not connected via context manager

        with pytest.raises(RuntimeError, match="Target not connected"):
            await target.create_folder("Test")


class TestImapTarget:
    def test_target_type(self):
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)
        assert target.target_type == "imap"


class TestSelectTarget:
    def test_select_websocket_for_local(self, mock_thunderbird_profile):
        mock_ws = MagicMock()
        mock_ws.is_connected = True

        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
            thunderbird=ThunderbirdConfig(profile_path=str(mock_thunderbird_profile)),
        )
        target = select_target(config, mock_ws, "local")
        assert isinstance(target, WebSocketTarget)

    def test_raises_for_local_without_websocket(self):
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
        )
        with pytest.raises(ValueError, match="requires WebSocket connection"):
            select_target(config, None, "local")

    def test_select_websocket_for_server_name(self, mock_thunderbird_profile):
        """Test selecting target by server name resolves to account ID."""
        mock_ws = MagicMock()
        mock_ws.is_connected = True

        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
            thunderbird=ThunderbirdConfig(profile_path=str(mock_thunderbird_profile)),
        )
        # Should resolve server name to account ID
        target = select_target(config, mock_ws, "imap.example.com")
        assert isinstance(target, WebSocketTarget)

    def test_raises_for_unknown_server(self, mock_thunderbird_profile):
        """Test that unknown server names raise an error."""
        mock_ws = MagicMock()
        mock_ws.is_connected = True

        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
            thunderbird=ThunderbirdConfig(profile_path=str(mock_thunderbird_profile)),
        )
        with pytest.raises(ValueError, match="not found in Thunderbird profile"):
            select_target(config, mock_ws, "unknown.server.com")

    def test_falls_back_to_imap_without_websocket(self):
        """Test that server names fall back to IMAP when WebSocket not available."""
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
            websocket=WebSocketConfig(enabled=True),
        )
        target = select_target(config, None, "outlook.office365.com")
        assert isinstance(target, ImapTarget)

    def test_select_imap_target_explicitly(self):
        """Test that target_account='imap' selects ImapTarget."""
        config = Config(
            imap=ImapConfig(host="imap.example.com"),
        )
        target = select_target(config, None, "imap")
        assert isinstance(target, ImapTarget)


class TestWebSocketTargetWithRawBytes:
    """Test that WebSocketTarget accepts but ignores raw_bytes."""

    @pytest.mark.asyncio
    async def test_copy_email_with_raw_bytes(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])

        async with WebSocketTarget(mock_ws, "local") as target:
            # raw_bytes should be accepted but ignored
            result = await target.copy_email("<msg@example.com>", "Inbox", raw_bytes=b"raw email")
            assert result is True

    @pytest.mark.asyncio
    async def test_move_email_with_raw_bytes(self):
        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_request = AsyncMock(side_effect=[
            MagicMock(ok=True, result={"accounts": [{"id": "acc1", "type": "none"}]}),
            MagicMock(ok=True, result={}),
        ])

        async with WebSocketTarget(mock_ws, "local") as target:
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


class TestEmailTargetProtocol:
    def test_websocket_target_implements_protocol(self):
        mock_ws = MagicMock()
        target = WebSocketTarget(mock_ws, "local")
        assert isinstance(target, EmailTargetProtocol)

    def test_imap_target_implements_protocol(self):
        config = ImapConfig(host="imap.example.com")
        target = ImapTarget(config)
        assert isinstance(target, EmailTargetProtocol)
