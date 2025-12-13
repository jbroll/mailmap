"""WebSocket email source (via Thunderbird extension).

This source is primarily for single message lookups when the
Thunderbird extension is connected. It's not efficient for bulk
operations - use ThunderbirdSource or ImapSource instead.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mailmap.email import UnifiedEmail

if TYPE_CHECKING:
    from mailmap.websocket_server import WebSocketServer


class WebSocketSource:
    """Email source via WebSocket connection to Thunderbird extension.

    This source requires a running WebSocket server with a connected
    Thunderbird extension. It's useful for retrieving individual
    messages by ID but not efficient for bulk operations.
    """

    def __init__(self, ws_server: "WebSocketServer"):
        """Initialize WebSocket source.

        Args:
            ws_server: Running WebSocket server instance
        """
        self._ws_server = ws_server

    @property
    def source_type(self) -> str:
        return "websocket"

    async def connect(self) -> None:
        """Verify WebSocket connection is available."""
        if not self._ws_server.is_connected:
            raise RuntimeError("No Thunderbird extension connected")

    async def disconnect(self) -> None:
        """No cleanup needed for WebSocket source."""
        pass

    async def list_folders(self) -> list[str]:
        """List folders via extension.

        Note: This returns classification folders, not source folders.
        For source folders, use ThunderbirdSource or ImapSource.
        """
        from mailmap.protocol import Action

        response = await self._ws_server.send_request(Action.LIST_FOLDERS, {})
        if response and response.ok and response.result:
            return response.result.get("folders", [])
        return []

    async def read_emails(
        self,
        folder: str,
        limit: int | None = None,
        random_sample: bool = False,
    ) -> AsyncIterator[UnifiedEmail]:
        """Read emails from a folder via extension.

        Note: WebSocket source is not efficient for bulk reads.
        Use ThunderbirdSource or ImapSource instead.

        This method raises NotImplementedError as bulk reading
        via WebSocket is not implemented.
        """
        raise NotImplementedError(
            "WebSocket source does not support bulk email reading. "
            "Use ThunderbirdSource or ImapSource for bulk operations."
        )
        # Make this an async generator
        yield  # pragma: no cover

    async def __aenter__(self) -> "WebSocketSource":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
