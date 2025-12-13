"""IMAP email source."""

import asyncio
import random
from collections.abc import AsyncIterator

from mailmap.config import ImapConfig
from mailmap.email import UnifiedEmail
from mailmap.imap_client import ImapMailbox


class ImapSource:
    """Email source reading directly from IMAP server.

    Use when Thunderbird cache is not available or when
    you need live server data.
    """

    def __init__(self, config: ImapConfig):
        """Initialize IMAP source.

        Args:
            config: IMAP connection configuration
        """
        self._config = config
        self._mailbox: ImapMailbox | None = None

    @property
    def source_type(self) -> str:
        return "imap"

    async def connect(self) -> None:
        """Connect to the IMAP server."""
        loop = asyncio.get_event_loop()
        self._mailbox = ImapMailbox(self._config)
        await loop.run_in_executor(None, self._mailbox.connect)

    async def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self._mailbox:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._mailbox.disconnect)
            self._mailbox = None

    async def list_folders(self) -> list[str]:
        """List available IMAP folders."""
        if self._mailbox is None:
            raise RuntimeError("Source not connected")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._mailbox.list_folders)

    async def read_emails(
        self,
        folder: str,
        limit: int | None = None,
        random_sample: bool = False,
    ) -> AsyncIterator[UnifiedEmail]:
        """Read emails from a folder.

        Args:
            folder: IMAP folder name
            limit: Maximum number of emails to read
            random_sample: If True, randomly sample from all UIDs

        Yields:
            UnifiedEmail objects
        """
        if self._mailbox is None:
            raise RuntimeError("Source not connected")

        loop = asyncio.get_event_loop()
        mailbox = self._mailbox  # Capture for lambda

        # Get UIDs
        await loop.run_in_executor(None, mailbox.select_folder, folder)
        all_uids = await loop.run_in_executor(
            None,
            lambda: list(mailbox.client.search(["ALL"])),
        )

        if not all_uids:
            return

        # Select UIDs to fetch
        if random_sample and limit and limit < len(all_uids):
            selected_uids = random.sample(all_uids, limit)
        elif limit:
            # Take most recent (highest UIDs)
            selected_uids = all_uids[-limit:]
        else:
            selected_uids = all_uids

        # Fetch emails
        for uid in selected_uids:
            email_msg = await loop.run_in_executor(
                None,
                self._mailbox.fetch_email,
                uid,
                folder,
            )
            if email_msg:
                yield UnifiedEmail.from_imap(
                    message_id=email_msg.message_id,
                    folder=email_msg.folder,
                    subject=email_msg.subject,
                    from_addr=email_msg.from_addr,
                    body_text=email_msg.body_text,
                    uid=email_msg.uid,
                )

    async def __aenter__(self) -> "ImapSource":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
