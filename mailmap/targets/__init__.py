"""Email target abstractions.

This module provides a unified interface for writing emails to various targets:
- WebSocketTarget: Copy/move via Thunderbird extension (supports Local Folders and IMAP)
- ImapTarget: Direct IMAP server writes

Use select_target() to automatically choose the best target based on
configuration and requirements.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from mailmap.config import Config
from mailmap.thunderbird import ThunderbirdReader, find_thunderbird_profile

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
    - For "local": Must use WebSocket (Local Folders only accessible via extension)
    - For server hostname: Resolve to account ID via Thunderbird profile, use WebSocket

    Args:
        config: Application configuration
        ws_server: Running WebSocket server (or None if not available)
        target_account: Target account:
            - "local": Thunderbird Local Folders (requires WebSocket)
            - Server hostname (e.g., "outlook.office365.com"): Resolved to account ID

    Returns:
        An EmailTarget instance (not yet connected)

    Raises:
        ValueError: If target account requires WebSocket but no connection available
    """
    # Check if WebSocket is available and connected
    ws_available = ws_server is not None and ws_server.is_connected

    # All targets require WebSocket (for copy/move via extension)
    if not ws_available or ws_server is None:
        raise ValueError(
            f"Target '{target_account}' requires WebSocket connection.\n"
            "Ensure the Thunderbird extension is installed and connected."
        )

    # Local Folders - use "local" keyword directly
    if target_account == "local":
        # Resolve "local" to actual account ID via profile
        profile_path = None
        if config.thunderbird.profile_path:
            profile_path = Path(config.thunderbird.profile_path)

        tb_profile = find_thunderbird_profile(profile_path)
        if tb_profile:
            try:
                reader = ThunderbirdReader(profile_path=tb_profile)
                account_id = reader.resolve_server_to_account_id("local")
                return WebSocketTarget(ws_server, account_id)
            except ValueError:
                pass  # Fall through to use "local" as-is

        # Fallback: let extension handle "local" (older behavior)
        return WebSocketTarget(ws_server, "local")

    # Server hostname - resolve to account ID
    profile_path = None
    if config.thunderbird.profile_path:
        profile_path = Path(config.thunderbird.profile_path)

    tb_profile = find_thunderbird_profile(profile_path)
    if not tb_profile:
        raise ValueError(
            f"Cannot resolve server '{target_account}' - no Thunderbird profile found.\n"
            "Set profile path in config.toml [thunderbird] section."
        )

    reader = ThunderbirdReader(profile_path=tb_profile)
    account_id = reader.resolve_server_to_account_id(target_account)
    return WebSocketTarget(ws_server, account_id)
