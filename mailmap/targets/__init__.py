"""Email target abstractions.

This module provides a unified interface for writing emails to various targets:
- WebSocketTarget: Copy/move via Thunderbird extension (supports Local Folders and IMAP)
- ImapTarget: Direct IMAP server writes

Use select_target() to automatically choose the best target based on
configuration and requirements.
"""

from mailmap.config import Config

from .base import EmailTarget
from .imap import ImapTarget
from .websocket import WebSocketTarget

__all__ = [
    "EmailTarget",
    "ImapTarget",
    "WebSocketTarget",
    "select_target",
]


def select_target(
    config: Config,
    target_account: str = "local",
    websocket_port: int | None = None,
) -> EmailTarget:
    """Select the best email target based on configuration.

    Selection logic:
    - "local" with websocket_port: WebSocket target to Thunderbird Local Folders
    - "local" without websocket_port: Error (requires WebSocket)
    - "imap": Direct IMAP connection
    - Other with websocket_port: WebSocket target to that account
    - Other without websocket_port: Direct IMAP connection

    Args:
        config: Application configuration
        target_account: Target account:
            - "local": Thunderbird Local Folders (requires websocket_port)
            - "imap": Direct IMAP connection
            - Server hostname or account ID: Uses WebSocket if port provided, else IMAP
        websocket_port: Port for WebSocket server (default port: 9753 if None but needed)

    Returns:
        An EmailTarget instance (not yet connected)

    Raises:
        ValueError: If target account requires WebSocket but not available
    """
    # "imap" always uses direct IMAP
    if target_account == "imap":
        return ImapTarget(config.imap)

    # "local" requires WebSocket
    if target_account == "local":
        if websocket_port is None:
            raise ValueError(
                "Target 'local' requires --websocket.\n"
                "Use --websocket to enable, or use --target-account imap for direct IMAP."
            )
        return WebSocketTarget(config, target_account, websocket_port)

    # Other target_account with websocket_port: use WebSocket
    if websocket_port is not None:
        return WebSocketTarget(config, target_account, websocket_port)

    # No WebSocket - use direct IMAP
    return ImapTarget(config.imap)
