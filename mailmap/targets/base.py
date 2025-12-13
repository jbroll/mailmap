"""Base protocol for email targets."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmailTarget(Protocol):
    """Protocol for email targets (IMAP, WebSocket)."""

    @property
    def target_type(self) -> str:
        """Return the target type identifier."""
        ...

    async def connect(self) -> None:
        """Establish connection to the target."""
        ...

    async def disconnect(self) -> None:
        """Close connection to the target."""
        ...

    async def create_folder(self, folder: str) -> bool:
        """Create a folder on the target.

        Args:
            folder: Folder name to create

        Returns:
            True if created, False if already exists
        """
        ...

    async def delete_folder(self, folder: str) -> bool:
        """Delete a folder from the target.

        Args:
            folder: Folder name to delete

        Returns:
            True if deleted, False if not found
        """
        ...

    async def copy_email(self, message_id: str, target_folder: str) -> bool:
        """Copy an email to a target folder.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder

        Returns:
            True if successful
        """
        ...

    async def move_email(self, message_id: str, target_folder: str) -> bool:
        """Move an email to a target folder.

        Args:
            message_id: Message-ID header of the email
            target_folder: Destination folder

        Returns:
            True if successful
        """
        ...

    async def __aenter__(self) -> "EmailTarget":
        """Async context manager entry."""
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        ...
