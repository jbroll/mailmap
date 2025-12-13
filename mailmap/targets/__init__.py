"""Email target abstractions.

This module provides a unified interface for writing emails to various targets:
- WebSocketTarget: Copy/move via Thunderbird extension (supports Local Folders and IMAP)
- ImapTarget: Direct IMAP server writes

Use select_target() to automatically choose the best target based on
configuration and requirements.
"""

from typing import TYPE_CHECKING

from mailmap.config import Config

from .base import EmailTarget
from .imap import ImapTarget
from .websocket import WebSocketTarget

if TYPE_CHECKING:
    from mailmap.websocket_server import WebSocketServer

__all__ = [
    "EmailTarget",
    "ImapTarget",
    "WebSocketTarget",
    "select_target",
]


def select_target(
    config: Config,
    ws_server: "WebSocketServer | None",
    target_account: str = "local",
) -> EmailTarget:
    """Select the best email target based on configuration.

    Selection logic:
    - For "local" account: Must use WebSocket (Local Folders only accessible via extension)
    - For "imap" account: Prefer WebSocket if connected, fall back to direct IMAP
    - For specific account ID: Must use WebSocket

    Args:
        config: Application configuration
        ws_server: Running WebSocket server (or None if not available)
        target_account: Target account type:
            - "local": Thunderbird Local Folders (requires WebSocket)
            - "imap": IMAP account (prefers WebSocket, falls back to direct IMAP)
            - Account ID: Specific Thunderbird account (requires WebSocket)

    Returns:
        An EmailTarget instance (not yet connected)

    Raises:
        ValueError: If target account requires WebSocket but no connection available
    """
    # Check if WebSocket is available and connected
    ws_available = ws_server is not None and ws_server.is_connected

    # Local Folders require WebSocket
    if target_account == "local":
        if not ws_available or ws_server is None:
            raise ValueError(
                "Target 'local' (Thunderbird Local Folders) requires WebSocket connection.\n"
                "Ensure the Thunderbird extension is installed and connected."
            )
        return WebSocketTarget(ws_server, target_account)

    # IMAP target - prefer WebSocket, fall back to direct IMAP
    if target_account == "imap":
        if ws_available and ws_server is not None:
            return WebSocketTarget(ws_server, target_account)
        elif config.imap.host:
            return ImapTarget(config.imap)
        else:
            raise ValueError(
                "No IMAP target available. Either:\n"
                "- Connect Thunderbird extension via WebSocket\n"
                "- Configure IMAP server in config.toml"
            )

    # Specific account ID - requires WebSocket
    if not ws_available or ws_server is None:
        raise ValueError(
            f"Target account '{target_account}' requires WebSocket connection.\n"
            "Ensure the Thunderbird extension is installed and connected."
        )
    return WebSocketTarget(ws_server, target_account)
