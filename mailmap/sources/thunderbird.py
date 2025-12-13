"""Thunderbird cache email source."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from mailmap.email import UnifiedEmail
from mailmap.thunderbird import ThunderbirdReader


class ThunderbirdSource:
    """Email source reading from Thunderbird's local mbox cache.

    This is the fastest source for bulk operations as it reads
    directly from local files without network access.
    """

    def __init__(
        self,
        profile_path: Path | None = None,
        server_filter: str | None = None,
    ):
        """Initialize Thunderbird source.

        Args:
            profile_path: Path to Thunderbird profile, or None to auto-detect
            server_filter: Optional IMAP server name to filter
        """
        self._profile_path = profile_path
        self._server_filter = server_filter
        self._reader: ThunderbirdReader | None = None

    @property
    def source_type(self) -> str:
        return "thunderbird"

    async def connect(self) -> None:
        """Initialize the Thunderbird reader."""
        # ThunderbirdReader is synchronous, run in executor
        loop = asyncio.get_event_loop()
        self._reader = await loop.run_in_executor(
            None,
            lambda: ThunderbirdReader(
                profile_path=self._profile_path,
                server_filter=self._server_filter,
            ),
        )

    async def disconnect(self) -> None:
        """Clean up resources."""
        self._reader = None

    async def list_folders(self) -> list[str]:
        """List available folders with server prefix if needed."""
        if self._reader is None:
            raise RuntimeError("Source not connected")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._reader.list_folders_qualified,
        )

    async def read_emails(
        self,
        folder: str,
        limit: int | None = None,
        random_sample: bool = False,
    ) -> AsyncIterator[UnifiedEmail]:
        """Read emails from a folder.

        Args:
            folder: Folder name or qualified name (server:folder)
            limit: Maximum number of emails to read
            random_sample: If True, randomly sample emails

        Yields:
            UnifiedEmail objects
        """
        if self._reader is None:
            raise RuntimeError("Source not connected")

        loop = asyncio.get_event_loop()
        reader = self._reader  # Capture for lambda

        # Read emails synchronously in executor
        if random_sample and limit:
            emails = await loop.run_in_executor(
                None,
                lambda: list(reader.read_folder_random(folder, limit)),
            )
        else:
            emails = await loop.run_in_executor(
                None,
                lambda: list(reader.read_folder(folder, limit)),
            )

        # Yield as UnifiedEmail
        for tb_email in emails:
            yield UnifiedEmail.from_thunderbird(tb_email)

    async def __aenter__(self) -> "ThunderbirdSource":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
