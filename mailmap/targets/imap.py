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

    Note: Copy operation requires re-fetching the email content,
    which is slower than WebSocket target's server-side copy.
    """

    def __init__(self, config: ImapConfig):
        """Initialize IMAP target.

        Args:
            config: IMAP connection configuration
        """
        self._config = config
        self._mailbox: ImapMailbox | None = None

    @property
    def target_type(self) -> str:
        return "imap"

    async def connect(self) -> None:
        """Connect to the IMAP server."""
        loop = asyncio.get_event_loop()
        self._mailbox = ImapMailbox(self._config)
        await loop.run_in_executor(None, self._mailbox.connect)
        logger.info(f"IMAP target connected to {self._config.host}")

    async def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self._mailbox:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._mailbox.disconnect)
            self._mailbox = None

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

    async def copy_email(self, message_id: str, target_folder: str) -> bool:
        """Copy an email to a target folder.

        This searches for the email by Message-ID, fetches it,
        and appends to the target folder.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder

        Returns:
            True if successful
        """
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        # Ensure target folder exists
        await loop.run_in_executor(
            None,
            self._mailbox.ensure_folder,
            target_folder,
        )

        # Search for message by Message-ID across all folders
        raw_email = await self._find_and_fetch_email(message_id)
        if not raw_email:
            logger.warning(f"Email not found: {message_id}")
            return False

        # Append to target folder
        try:
            await loop.run_in_executor(
                None,
                self._mailbox.append_email,
                target_folder,
                raw_email,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to copy {message_id}: {e}")
            return False

    async def move_email(self, message_id: str, target_folder: str) -> bool:
        """Move an email to a target folder.

        This searches for the email by Message-ID and uses IMAP MOVE
        if available, otherwise copies and deletes.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder

        Returns:
            True if successful
        """
        if self._mailbox is None:
            raise RuntimeError("Target not connected")

        loop = asyncio.get_event_loop()

        # Ensure target folder exists
        await loop.run_in_executor(
            None,
            self._mailbox.ensure_folder,
            target_folder,
        )

        # Find the email
        location = await self._find_email(message_id)
        if not location:
            logger.warning(f"Email not found: {message_id}")
            return False

        source_folder, uid = location

        # Use IMAP MOVE command
        try:
            await loop.run_in_executor(
                None,
                self._mailbox.move_email,
                uid,
                source_folder,
                target_folder,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to move {message_id}: {e}")
            return False

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

    async def _find_and_fetch_email(self, message_id: str) -> bytes | None:
        """Find and fetch raw email by Message-ID.

        Args:
            message_id: Message-ID header to search for

        Returns:
            Raw email bytes if found, None otherwise
        """
        if self._mailbox is None:
            return None

        location = await self._find_email(message_id)
        if not location:
            return None

        folder, uid = location
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

    async def __aenter__(self) -> "ImapTarget":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
