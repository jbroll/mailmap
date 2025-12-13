"""Base protocol for email sources."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from mailmap.email import UnifiedEmail


@runtime_checkable
class EmailSource(Protocol):
    """Protocol for email sources (IMAP, Thunderbird, WebSocket)."""

    @property
    def source_type(self) -> str:
        """Return the source type identifier."""
        ...

    async def connect(self) -> None:
        """Establish connection to the source."""
        ...

    async def disconnect(self) -> None:
        """Close connection to the source."""
        ...

    async def list_folders(self) -> list[str]:
        """List available folders."""
        ...

    def read_emails(
        self,
        folder: str,
        limit: int | None = None,
        random_sample: bool = False,
    ) -> AsyncIterator[UnifiedEmail]:
        """Read emails from a folder.

        Args:
            folder: Folder name or qualified name (server:folder)
            limit: Maximum number of emails to read
            random_sample: If True, randomly sample emails instead of sequential

        Yields:
            UnifiedEmail objects
        """
        ...

    async def __aenter__(self) -> "EmailSource":
        """Async context manager entry."""
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        ...
