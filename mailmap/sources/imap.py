"""IMAP email source."""

import asyncio
import random
from collections.abc import AsyncIterator

from mailmap.config import ImapConfig
from mailmap.email import UnifiedEmail
from mailmap.imap_client import ImapClient


class ImapSource:
    """Email source reading directly from IMAP server.

    Use when Thunderbird cache is not available or when
    you need live server data.
    """

    def __init__(self, config: ImapConfig):
        self._config = config
        self._client: ImapClient | None = None

    @property
    def source_type(self) -> str:
        return "imap"

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._client = ImapClient(self._config)
        await loop.run_in_executor(None, self._client.connect)

    async def disconnect(self) -> None:
        if self._client:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._client.disconnect)
            self._client = None

    async def list_folders(self) -> list[str]:
        if self._client is None:
            raise RuntimeError("Source not connected")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._client.list_folders)

    async def read_emails(
        self,
        folder: str,
        limit: int | None = None,
        random_sample: bool = False,
    ) -> AsyncIterator[UnifiedEmail]:
        """Read emails from a folder.

        Yields:
            UnifiedEmail objects
        """
        if self._client is None:
            raise RuntimeError("Source not connected")

        loop = asyncio.get_event_loop()

        all_uids = await loop.run_in_executor(None, self._client.fetch_uids, folder)

        if not all_uids:
            return

        if random_sample and limit and limit < len(all_uids):
            selected_uids = random.sample(all_uids, limit)
        elif limit:
            selected_uids = all_uids[-limit:]
        else:
            selected_uids = all_uids

        for uid in selected_uids:
            msg = await loop.run_in_executor(None, self._client.fetch_email, uid, folder)
            if msg:
                yield UnifiedEmail.from_imap(
                    message_id=msg["message_id"],
                    folder=msg["folder"],
                    subject=msg["subject"],
                    from_addr=msg["from"],
                    body_text=msg["body"],
                    uid=msg["uid"],
                    attachments=msg.get("attachments") or None,
                )

    async def __aenter__(self) -> "ImapSource":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
