"""Thunderbird profile detection and parsing utilities."""

import configparser
import logging
import re
from pathlib import Path

logger = logging.getLogger("mailmap")


def find_thunderbird_profile(base_path: Path | None = None) -> Path | None:
    """Find the default Thunderbird profile directory."""
    if base_path is not None:
        # Explicit path provided - use it or fail, don't fall back to auto-detection
        if base_path.exists():
            return base_path
        return None

    # Default Thunderbird locations
    candidates = [
        Path.home() / ".thunderbird",
        Path.home() / ".mozilla-thunderbird",
        Path.home() / "snap/thunderbird/common/.thunderbird",
    ]

    for candidate in candidates:
        if candidate.exists():
            # Look for profiles.ini or just find first profile directory
            profiles_ini = candidate / "profiles.ini"
            if profiles_ini.exists():
                # Parse profiles.ini to find default profile
                config = configparser.ConfigParser()
                config.read(profiles_ini)

                for section in config.sections():
                    if section.startswith("Profile") or section.startswith("Install"):
                        if config.has_option(section, "Default") and config.get(section, "Default") == "1":
                            if config.has_option(section, "Path"):
                                path = config.get(section, "Path")
                                if config.has_option(section, "IsRelative") and config.get(section, "IsRelative") == "1":
                                    return candidate / path
                                return Path(path)
                        elif config.has_option(section, "Path"):
                            # Fallback to first profile found
                            path = config.get(section, "Path")
                            if config.has_option(section, "IsRelative") and config.get(section, "IsRelative") == "1":
                                return candidate / path
                            return Path(path)

            # Fallback: find first .default profile directory
            for profile_dir in candidate.iterdir():
                if profile_dir.is_dir() and ".default" in profile_dir.name:
                    return profile_dir

    return None


def find_imap_mail_dirs(profile_path: Path) -> list[Path]:
    """Find all ImapMail directories in a Thunderbird profile."""
    imap_mail = profile_path / "ImapMail"
    if not imap_mail.exists():
        return []

    return [d for d in imap_mail.iterdir() if d.is_dir()]


def parse_prefs_js(profile_path: Path) -> dict[str, str]:
    """Parse Thunderbird's prefs.js file into a dict of preferences.

    Args:
        profile_path: Path to the Thunderbird profile directory

    Returns:
        Dict of preference name -> value
    """
    prefs_path = profile_path / "prefs.js"
    if not prefs_path.exists():
        return {}

    prefs = {}

    # Pattern: user_pref("key", value);
    # Value can be string (quoted), number, or boolean
    pref_pattern = re.compile(r'user_pref\("([^"]+)",\s*(.+)\);')

    try:
        with open(prefs_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                match = pref_pattern.match(line.strip())
                if match:
                    key = match.group(1)
                    value = match.group(2).strip()
                    # Remove quotes from string values
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    prefs[key] = value
    except Exception as e:
        logger.warning(f"Failed to parse prefs.js: {e}")

    return prefs


def get_account_server_mapping(profile_path: Path) -> dict[str, str]:
    """Get mapping of server hostnames to Thunderbird account IDs.

    Args:
        profile_path: Path to the Thunderbird profile directory

    Returns:
        Dict of server hostname -> account ID (e.g., {"outlook.office365.com": "account1"})
    """
    prefs = parse_prefs_js(profile_path)

    # Build server ID -> hostname mapping
    server_hostnames: dict[str, str] = {}
    for key, value in prefs.items():
        if key.startswith("mail.server.") and key.endswith(".hostname"):
            # Extract server ID: mail.server.server2.hostname -> server2
            parts = key.split(".")
            if len(parts) >= 3:
                server_id = parts[2]
                server_hostnames[server_id] = value

    # Build account ID -> server ID mapping
    account_servers: dict[str, str] = {}
    for key, value in prefs.items():
        if key.startswith("mail.account.") and key.endswith(".server"):
            # Extract account ID: mail.account.account1.server -> account1
            parts = key.split(".")
            if len(parts) >= 3:
                account_id = parts[2]
                account_servers[account_id] = value

    # Combine: hostname -> account ID
    hostname_to_account: dict[str, str] = {}
    for account_id, server_id in account_servers.items():
        if server_id in server_hostnames:
            hostname = server_hostnames[server_id]
            hostname_to_account[hostname] = account_id

    # Also handle Local Folders (type = "none")
    local_folders_server = prefs.get("mail.accountmanager.localfoldersserver")
    if local_folders_server:
        # Find the account that uses this server
        for account_id, server_id in account_servers.items():
            if server_id == local_folders_server:
                hostname_to_account["local"] = account_id
                break

    return hostname_to_account
