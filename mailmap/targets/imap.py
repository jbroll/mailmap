"""IMAP email target."""

import asyncio
import logging

from mailmap.config import ImapConfig
from mailmap.imap_client import ImapMailbox

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
        """Initialize IMAP target.

        Args:
            config: IMAP connection configuration
        """
        self._config = config
        self._mailbox: ImapMailbox | None = None
        self._ensured_folders: set[str] = set()  # Cache of folders we've ensured exist
        self._reconnect_attempt = 0

    @property
    def target_type(self) -> str:
        return "imap"

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        delay = self.INITIAL_RETRY_DELAY * (self.BACKOFF_MULTIPLIER ** attempt)
        return min(delay, self.MAX_RETRY_DELAY)

    async def connect(self) -> None:
        """Connect to the IMAP server."""
        loop = asyncio.get_event_loop()
        self._mailbox = ImapMailbox(self._config)
        await loop.run_in_executor(None, self._mailbox.connect)
        self._reconnect_attempt = 0  # Reset on successful connect
        logger.info(f"IMAP target connected to {self._config.host}")

    async def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self._mailbox:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._mailbox.disconnect)
            self._mailbox = None

    async def _reconnect(self) -> bool:
        """Attempt to reconnect to the IMAP server.

        Returns:
            True if reconnection succeeded, False otherwise
        """
        logger.info("Attempting IMAP reconnection...")

        # Disconnect existing connection if any
        if self._mailbox:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._mailbox.disconnect)
            except Exception:
                pass
            self._mailbox = None

        # Clear folder cache since connection state is lost
        self._ensured_folders.clear()

        try:
            await self.connect()
            logger.info("IMAP reconnection successful")
            return True
        except Exception as e:
            logger.warning(f"IMAP reconnection failed: {e}")
            return False

    def _is_connection_error(self, error: Exception) -> bool:
        """Check if an exception indicates a connection problem.

        Args:
            error: The exception to check

        Returns:
            True if the error suggests connection issues that might be fixed by reconnecting
        """
        error_str = str(error).lower()

        # Known connection-related error patterns
        connection_patterns = [
            "connection",
            "socket",
            "eof",
            "broken pipe",
            "reset by peer",
            "timed out",
            "bad command",
            "unknown command",  # The error pattern the user saw
            "not connected",
            "server unavailable",
        ]

        return any(pattern in error_str for pattern in connection_patterns)

    async def create_folder(self, folder: str) -> bool:
        """Create a folder on the IMAP server.

        Args:
            folder: Folder name to create

        Returns:
            True if created, False if already exists
        """
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._mailbox.create_folder,
            folder,
        )

    async def delete_folder(self, folder: str) -> bool:
        """Delete a folder from the IMAP server.

        Note: Most IMAP servers require the folder to be empty first.

        Args:
            folder: Folder name to delete

        Returns:
            True if deleted, False otherwise
        """
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                self._mailbox.client.delete_folder,
                folder,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder}: {e}")
            return False

    async def list_folders(self) -> list[str]:
        """List all folders on the IMAP server.

        Returns:
            List of folder names
        """
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._mailbox.list_folders)

    async def copy_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Copy an email to a target folder.

        If raw_bytes is provided, uploads directly (for cross-server transfers).
        Otherwise searches for the email by Message-ID on this server.

        Includes automatic reconnection on connection errors.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder
            raw_bytes: Optional raw email content for cross-server uploads

        Returns:
            True if successful
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
        """Internal implementation of copy_email without retry logic."""
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        # Ensure target folder exists (cached to avoid redundant calls)
        if target_folder not in self._ensured_folders:
            await loop.run_in_executor(
                None,
                self._mailbox.ensure_folder,
                target_folder,
            )
            self._ensured_folders.add(target_folder)

        # Use provided raw bytes or search for email on server
        raw_email = raw_bytes
        source_folder = None
        if raw_email is None:
            location = await self._find_email(message_id)
            if not location:
                logger.warning(f"Email not found: {message_id}")
                return False
            source_folder, uid = location

            # Skip if email is already in target folder
            if source_folder == target_folder:
                logger.debug(f"Email already in {target_folder}: {message_id}")
                return True

            raw_email = await self._fetch_email_by_uid(source_folder, uid)
            if not raw_email:
                logger.warning(f"Failed to fetch email: {message_id}")
                return False

        # Append to target folder
        await loop.run_in_executor(
            None,
            self._mailbox.append_email,
            target_folder,
            raw_email,
        )
        return True

    async def move_email(
        self, message_id: str, target_folder: str, raw_bytes: bytes | None = None
    ) -> bool:
        """Move an email to a target folder.

        If raw_bytes is provided, uploads directly (for cross-server transfers).
        Note: cross-server "move" only uploads; source deletion must be handled separately.
        Otherwise searches for the email by Message-ID and uses IMAP MOVE.

        Includes automatic reconnection on connection errors.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder
            raw_bytes: Optional raw email content for cross-server uploads

        Returns:
            True if successful
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
        """Internal implementation of move_email without retry logic."""
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        # Ensure target folder exists (cached to avoid redundant calls)
        if target_folder not in self._ensured_folders:
            await loop.run_in_executor(
                None,
                self._mailbox.ensure_folder,
                target_folder,
            )
            self._ensured_folders.add(target_folder)

        # If raw bytes provided, upload directly (cross-server transfer)
        if raw_bytes is not None:
            await loop.run_in_executor(
                None,
                self._mailbox.append_email,
                target_folder,
                raw_bytes,
            )
            return True

        # Find the email on this server
        location = await self._find_email(message_id)
        if not location:
            logger.warning(f"Email not found: {message_id}")
            return False

        source_folder, uid = location

        # Use IMAP MOVE command
        await loop.run_in_executor(
            None,
            self._mailbox.move_email,
            uid,
            source_folder,
            target_folder,
        )
        return True

    async def _find_email(self, message_id: str) -> tuple[str, int] | None:
        """Find an email by Message-ID across all folders.

        Args:
            message_id: Message-ID header to search for

        Returns:
            Tuple of (folder, uid) if found, None otherwise
        """
        if self._mailbox is None:
            return None

        loop = asyncio.get_event_loop()
        mailbox = self._mailbox  # Capture for lambda

        # Get all folders
        folders = await loop.run_in_executor(None, mailbox.list_folders)

        # Search each folder
        for folder in folders:
            try:
                await loop.run_in_executor(
                    None,
                    mailbox.select_folder,
                    folder,
                )

                # Search by Message-ID header
                uids = await loop.run_in_executor(
                    None,
                    lambda: mailbox.client.search(["HEADER", "Message-ID", message_id]),
                )

                if uids:
                    return (folder, uids[0])
            except Exception as e:
                logger.debug(f"Error searching {folder}: {e}")
                continue

        return None

    async def _fetch_email_by_uid(self, folder: str, uid: int) -> bytes | None:
        """Fetch raw email by folder and UID.

        Args:
            folder: Folder containing the email
            uid: UID of the email

        Returns:
            Raw email bytes if found, None otherwise
        """
        if self._mailbox is None:
            return None

        loop = asyncio.get_event_loop()
        mailbox = self._mailbox  # Capture for lambda

        await loop.run_in_executor(
            None,
            mailbox.select_folder,
            folder,
        )

        messages = await loop.run_in_executor(
            None,
            # Use BODY.PEEK[] to avoid marking as read
            lambda: mailbox.client.fetch([uid], ["BODY.PEEK[]"]),
        )

        if uid in messages:
            return messages[uid][b"BODY[]"]

        return None

    async def _find_and_fetch_email(self, message_id: str) -> bytes | None:
        """Find and fetch raw email by Message-ID.

        Args:
            message_id: Message-ID header to search for

        Returns:
            Raw email bytes if found, None otherwise
        """
        location = await self._find_email(message_id)
        if not location:
            return None

        folder, uid = location
        return await self._fetch_email_by_uid(folder, uid)

    async def __aenter__(self) -> "ImapTarget":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
