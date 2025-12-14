"""WebSocket email target (via Thunderbird extension)."""

import logging
from typing import TYPE_CHECKING

from mailmap.protocol import Action

if TYPE_CHECKING:
    from mailmap.websocket_server import WebSocketServer

logger = logging.getLogger("mailmap.targets.websocket")


class WebSocketTarget:
    """Email target via WebSocket connection to Thunderbird extension.

    This target requires a running WebSocket server with a connected
    Thunderbird extension. It can copy/move emails to both Local Folders
    and IMAP accounts configured in Thunderbird.
    """

    def __init__(self, ws_server: "WebSocketServer", target_account: str = "local"):
        """Initialize WebSocket target.

        Args:
            ws_server: Running WebSocket server instance
            target_account: Target account type:
                - "local": Thunderbird Local Folders
                - "imap": First IMAP account
                - Account ID: Specific Thunderbird account ID
        """
        self._ws_server = ws_server
        self._target_account = target_account
        self._account_id: str | None = None

    @property
    def target_type(self) -> str:
        return "websocket"

    async def connect(self) -> None:
        """Verify WebSocket connection and resolve account ID."""
        if not self._ws_server.is_connected:
            raise RuntimeError("No Thunderbird extension connected")

        # Resolve target account to account ID
        if self._target_account in ("local", "imap"):
            response = await self._ws_server.send_request(Action.LIST_ACCOUNTS, {})
            if not response or not response.ok:
                raise RuntimeError("Failed to list Thunderbird accounts")

            accounts = (response.result or {}).get("accounts", [])
            if not accounts:
                raise RuntimeError("No accounts found in Thunderbird")

            for account in accounts:
                account_type = account.get("type", "")
                if (
                    (self._target_account == "local" and account_type == "none")
                    or (self._target_account == "imap" and account_type == "imap")
                ):
                    self._account_id = account["id"]
                    break

            if not self._account_id:
                raise RuntimeError(
                    f"No {self._target_account} account found in Thunderbird"
                )
        else:
            # Direct account ID
            self._account_id = self._target_account

        logger.info(f"WebSocket target connected to account: {self._account_id}")

    async def disconnect(self) -> None:
        """No cleanup needed for WebSocket target."""
        self._account_id = None

    async def create_folder(self, folder: str) -> bool:
        """Create a folder via extension.

        Args:
            folder: Folder name to create

        Returns:
            True if created, False if already exists
        """
        if not self._account_id:
            raise RuntimeError("Target not connected")

        response = await self._ws_server.send_request(
            Action.CREATE_FOLDER,
            {"accountId": self._account_id, "name": folder},
        )
        if response and response.ok:
            return (response.result or {}).get("created", False)
        elif response and response.error:
            # Folder might already exist
            if "already exists" in response.error.lower():
                return False
            logger.error(f"Failed to create folder {folder}: {response.error}")
        return False

    async def delete_folder(self, folder: str) -> bool:
        """Delete a folder via extension.

        Args:
            folder: Folder name to delete

        Returns:
            True if deleted, False if not found
        """
        if not self._account_id:
            raise RuntimeError("Target not connected")

        response = await self._ws_server.send_request(
            Action.DELETE_FOLDER,
            {"accountId": self._account_id, "name": folder},
        )
        if response and response.ok:
            return (response.result or {}).get("deleted", False)
        elif response and response.error:
            logger.error(f"Failed to delete folder {folder}: {response.error}")
        return False

    async def copy_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Copy an email to a target folder via extension.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder
            raw_bytes: Ignored - WebSocket uses Thunderbird's native copy

        Returns:
            True if successful
        """
        if not self._account_id:
            raise RuntimeError("Target not connected")

        response = await self._ws_server.send_request(
            Action.COPY_MESSAGES,
            {
                "messageIds": [message_id],
                "accountId": self._account_id,
                "folder": target_folder,
            },
        )
        if response and response.ok:
            return True
        elif response and response.error:
            logger.warning(f"Failed to copy {message_id}: {response.error}")
        return False

    async def move_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Move an email to a target folder via extension.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder
            raw_bytes: Ignored - WebSocket uses Thunderbird's native move

        Returns:
            True if successful
        """
        if not self._account_id:
            raise RuntimeError("Target not connected")

        response = await self._ws_server.send_request(
            Action.MOVE_MESSAGES,
            {
                "messageIds": [message_id],
                "accountId": self._account_id,
                "folder": target_folder,
            },
        )
        if response and response.ok:
            return True
        elif response and response.error:
            logger.warning(f"Failed to move {message_id}: {response.error}")
        return False

    async def __aenter__(self) -> "WebSocketTarget":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
