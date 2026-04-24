"""IMAP email target."""

import asyncio
import logging

from mailmap.config import ImapConfig
from mailmap.imap_client import ImapClient

logger = logging.getLogger("mailmap.targets.imap")


class ImapTarget:
    """Email target writing directly to IMAP server.

    This target requires direct IMAP access and can create folders,
    copy emails (by re-fetching and appending), and move emails.

    Includes automatic reconnection with exponential backoff on connection failures.

    Note: Copy operation requires re-fetching the email content,
    which is slower than WebSocket target's server-side copy.
    """

    # Reconnection settings
    INITIAL_RETRY_DELAY = 1.0  # seconds
    MAX_RETRY_DELAY = 30.0  # max delay between retries
    BACKOFF_MULTIPLIER = 2.0
    MAX_RETRIES = 3

    def __init__(self, config: ImapConfig):
        self._config = config
        self._client: ImapClient | None = None
        self._ensured_folders: set[str] = set()  # Cache of folders we've ensured exist
        self._reconnect_attempt = 0

    @property
    def target_type(self) -> str:
        return "imap"

    def _calculate_backoff(self, attempt: int) -> float:
        delay = self.INITIAL_RETRY_DELAY * (self.BACKOFF_MULTIPLIER ** attempt)
        return min(delay, self.MAX_RETRY_DELAY)

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._client = ImapClient(self._config)
        await loop.run_in_executor(None, self._client.connect)
        self._reconnect_attempt = 0
        logger.info(f"IMAP target connected to {self._config.host}")

    async def disconnect(self) -> None:
        if self._client:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._client.disconnect)
            self._client = None

    async def _reconnect(self) -> bool:
        logger.info("Attempting IMAP reconnection...")
        if self._client:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._client.disconnect)
            except Exception:
                pass
            self._client = None
        self._ensured_folders.clear()
        try:
            await self.connect()
            logger.info("IMAP reconnection successful")
            return True
        except Exception as e:
            logger.warning(f"IMAP reconnection failed: {e}")
            return False

    def _is_connection_error(self, error: Exception) -> bool:
        error_str = str(error).lower()
        connection_patterns = [
            "connection", "socket", "eof", "broken pipe", "reset by peer",
            "timed out", "bad command", "unknown command", "not connected",
            "server unavailable",
        ]
        return any(pattern in error_str for pattern in connection_patterns)

    async def create_folder(self, folder: str) -> bool:
        if self._client is None:
            raise RuntimeError("Target not connected")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._client.create_folder, folder)

    async def delete_folder(self, folder: str) -> bool:
        if self._client is None:
            raise RuntimeError("Target not connected")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._client.delete_folder, folder)
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder}: {e}")
            return False

    async def list_folders(self) -> list[str]:
        if self._client is None:
            raise RuntimeError("Target not connected")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._client.list_folders)

    async def copy_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Copy an email to a target folder.

        If raw_bytes is provided, uploads directly (for cross-server transfers).
        Otherwise searches for the email by Message-ID on this server.
        """
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await self._copy_email_impl(message_id, target_folder, raw_bytes)
            except Exception as e:
                if not self._is_connection_error(e) or attempt >= self.MAX_RETRIES:
                    logger.error(f"Failed to copy {message_id}: {e}")
                    return False

                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Connection error copying {message_id}: {e}. "
                    f"Reconnecting in {delay:.1f}s (attempt {attempt + 1}/{self.MAX_RETRIES})..."
                )
                await asyncio.sleep(delay)

                if not await self._reconnect():
                    logger.error(f"Failed to reconnect after error copying {message_id}")
                    return False

        return False

    async def _copy_email_impl(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        if self._client is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        if target_folder not in self._ensured_folders:
            await loop.run_in_executor(None, self._client.ensure_folder, target_folder)
            self._ensured_folders.add(target_folder)

        raw_email = raw_bytes
        if raw_email is None:
            location = await self._find_email(message_id)
            if not location:
                logger.warning(f"Email not found: {message_id}")
                return False
            source_folder, uid = location

            if source_folder == target_folder:
                logger.debug(f"Email already in {target_folder}: {message_id}")
                return True

            raw_email = await loop.run_in_executor(
                None, self._client.fetch_raw, uid, source_folder
            )
            if not raw_email:
                logger.warning(f"Failed to fetch email: {message_id}")
                return False

        await loop.run_in_executor(None, self._client.append_email, target_folder, raw_email)
        return True

    async def move_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Move an email to a target folder.

        If raw_bytes is provided, uploads directly (for cross-server transfers).
        Otherwise searches for the email by Message-ID and uses IMAP MOVE.
        """
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return await self._move_email_impl(message_id, target_folder, raw_bytes)
            except Exception as e:
                if not self._is_connection_error(e) or attempt >= self.MAX_RETRIES:
                    logger.error(f"Failed to move {message_id}: {e}")
                    return False

                delay = self._calculate_backoff(attempt)
                logger.warning(
                    f"Connection error moving {message_id}: {e}. "
                    f"Reconnecting in {delay:.1f}s (attempt {attempt + 1}/{self.MAX_RETRIES})..."
                )
                await asyncio.sleep(delay)

                if not await self._reconnect():
                    logger.error(f"Failed to reconnect after error moving {message_id}")
                    return False

        return False

    async def _move_email_impl(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        if self._client is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        if target_folder not in self._ensured_folders:
            await loop.run_in_executor(None, self._client.ensure_folder, target_folder)
            self._ensured_folders.add(target_folder)

        if raw_bytes is not None:
            await loop.run_in_executor(None, self._client.append_email, target_folder, raw_bytes)
            return True

        location = await self._find_email(message_id)
        if not location:
            logger.warning(f"Email not found: {message_id}")
            return False

        source_folder, uid = location
        await loop.run_in_executor(
            None, self._client.move_email, uid, source_folder, target_folder
        )
        return True

    async def _find_email(self, message_id: str) -> tuple[str, int] | None:
        """Find an email by Message-ID across all folders."""
        if self._client is None:
            return None

        loop = asyncio.get_event_loop()
        client = self._client

        folders = await loop.run_in_executor(None, client.list_folders)

        for folder in folders:
            try:
                uids = await loop.run_in_executor(
                    None,
                    client.search,
                    folder,
                    ["HEADER", "Message-ID", message_id],
                )
                if uids:
                    return (folder, uids[0])
            except Exception as e:
                logger.debug(f"Error searching {folder}: {e}")
                continue

        return None

    async def __aenter__(self) -> "ImapTarget":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
