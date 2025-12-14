"""WebSocket email target (via Thunderbird extension)."""

import asyncio
import logging

from mailmap.config import Config, WebSocketConfig
from mailmap.protocol import Action
from mailmap.websocket_server import WebSocketServer

logger = logging.getLogger("mailmap.targets.websocket")


class WebSocketTarget:
    """Email target via WebSocket connection to Thunderbird extension.

    This target manages its own WebSocket server lifecycle. It starts the server
    on connect() and waits for a Thunderbird extension to connect. It can copy/move
    emails to both Local Folders and IMAP accounts configured in Thunderbird.
    """

    def __init__(
        self,
        config: Config,
        target_account: str = "local",
        websocket_port: int = 9753,
    ):
        """Initialize WebSocket target.

        Args:
            config: Application configuration
            target_account: Target account type:
                - "local": Thunderbird Local Folders
                - "imap": First IMAP account in Thunderbird
                - Account ID: Specific Thunderbird account ID
            websocket_port: Port for WebSocket server (default: 9753)
        """
        self._config = config
        self._target_account = target_account
        self._port = websocket_port
        self._account_id: str | None = None
        self._ws_server: WebSocketServer | None = None
        self._server_task: asyncio.Task | None = None

    @property
    def target_type(self) -> str:
        return "websocket"

    async def connect(self) -> None:
        """Start WebSocket server and wait for extension to connect."""
        from mailmap.database import Database
        from mailmap.websocket_server import start_websocket_and_wait

        # Create WebSocket config
        ws_config = WebSocketConfig(
            enabled=True,
            host="localhost",
            port=self._port,
            auth_token=self._config.websocket.auth_token if self._config.websocket else "",
        )

        # Need a database for the WebSocket server
        db = Database(self._config.database.path)

        # Start server and wait for connection
        result = await start_websocket_and_wait(
            ws_config, db, self._config.database.categories_file, timeout=30
        )
        if result is None:
            raise RuntimeError(
                "Timeout waiting for Thunderbird extension to connect.\n"
                "Make sure the MailMap extension is installed and enabled."
            )

        self._ws_server, self._server_task = result

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
        """Stop WebSocket server and cleanup."""
        self._account_id = None

        if self._ws_server:
            await self._ws_server.stop()
            self._ws_server = None

        if self._server_task:
            self._server_task.cancel()
            self._server_task = None

    async def create_folder(self, folder: str) -> bool:
        """Create a folder via extension.

        Args:
            folder: Folder name to create

        Returns:
            True if created, False if already exists
        """
        if not self._account_id or not self._ws_server:
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
        if not self._account_id or not self._ws_server:
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

    async def list_folders(self) -> list[str]:
        """List all folders via extension.

        Returns:
            List of folder names
        """
        if not self._account_id or not self._ws_server:
            raise RuntimeError("Target not connected")

        response = await self._ws_server.send_request(
            Action.LIST_FOLDERS,
            {"accountId": self._account_id},
        )
        if response and response.ok:
            folders = (response.result or {}).get("folders", [])
            return [f.get("name", "") for f in folders if f.get("name")]
        elif response and response.error:
            logger.error(f"Failed to list folders: {response.error}")
        return []

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
        if not self._account_id or not self._ws_server:
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
        if not self._account_id or not self._ws_server:
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
