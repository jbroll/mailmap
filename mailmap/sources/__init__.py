"""Email source abstractions.

This module provides a unified interface for reading emails from various sources:
- ThunderbirdSource: Fast local access from Thunderbird's mbox cache
- ImapSource: Direct IMAP server access for live data
- WebSocketSource: Single message lookups via Thunderbird extension

Use select_source() to automatically choose the best source based on
configuration and availability.
"""

from pathlib import Path

from mailmap.config import Config
from mailmap.thunderbird import find_thunderbird_profile

from .base import EmailSource
from .imap import ImapSource
from .thunderbird import ThunderbirdSource
from .websocket import WebSocketSource

__all__ = [
    "EmailSource",
    "ImapSource",
    "ThunderbirdSource",
    "WebSocketSource",
    "select_source",
]


def select_source(config: Config, source_type: str | None = None) -> EmailSource:
    """Select the best email source based on configuration.

    Selection logic:
    1. If source_type specified, use that source
    2. Otherwise prefer Thunderbird cache (fast, local, no network)
    3. Fall back to IMAP if Thunderbird not available
    4. Raise error if neither available

    Args:
        config: Application configuration
        source_type: Optional source type to force: 'thunderbird' or 'imap'

    Returns:
        An EmailSource instance (not yet connected)

    Raises:
        ValueError: If no email source is available or requested source unavailable
    """
    profile_path = None
    if config.thunderbird.profile_path:
        profile_path = Path(config.thunderbird.profile_path)

    # If source_type explicitly requested, use that
    if source_type == "imap":
        if not config.imap.host:
            raise ValueError(
                "IMAP source requested but not configured.\n"
                "Add IMAP server settings to config.toml [imap] section."
            )
        return ImapSource(config.imap)

    if source_type == "thunderbird":
        thunderbird_profile = find_thunderbird_profile(profile_path)
        if not thunderbird_profile:
            raise ValueError(
                "Thunderbird source requested but no profile found.\n"
                "Set profile path in config.toml [thunderbird] section."
            )
        return ThunderbirdSource(
            profile_path=thunderbird_profile,
            server_filter=config.thunderbird.server_filter,
        )

    # Auto-select: try Thunderbird first (fast local access)
    thunderbird_profile = find_thunderbird_profile(profile_path)
    if thunderbird_profile:
        return ThunderbirdSource(
            profile_path=thunderbird_profile,
            server_filter=config.thunderbird.server_filter,
        )

    # Fall back to IMAP
    if config.imap.host:
        return ImapSource(config.imap)

    raise ValueError(
        "No email source available. Configure either:\n"
        "- Thunderbird profile path in config.toml [thunderbird] section\n"
        "- IMAP server in config.toml [imap] section"
    )
