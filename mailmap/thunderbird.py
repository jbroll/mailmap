"""Thunderbird profile reader for importing existing emails."""

import logging
import mailbox
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .imap_client import decode_mime_header, extract_body

logger = logging.getLogger("mailmap")

# Headers to extract for spam detection
SPAM_HEADERS = [
    # Microsoft/Office 365
    "X-MS-Exchange-Organization-SCL",
    "X-Microsoft-Antispam",
    "X-Forefront-Antispam-Report",
    # SpamAssassin
    "X-Spam-Status",
    "X-Spam-Flag",
    "X-Spam-Score",
    "X-Spam-Level",
    # Rspamd
    "X-Rspamd-Score",
    "X-Rspamd-Action",
    "X-Spamd-Result",
    # Barracuda
    "X-Barracuda-Spam-Score",
    "X-Barracuda-Spam-Status",
    # SpamExperts / Spampanel
    "X-SpamExperts-Class",
    "X-SpamExperts-Outgoing-Class",
    "X-Spampanel-Outgoing-Class",
    # Proofpoint
    "X-Proofpoint-Spam-Details",
    # Cisco IronPort
    "X-IronPort-Anti-Spam-Result",
    # Trend Micro
    "X-TM-AS-Result",
    "X-TMASE-Result",
    # Mimecast
    "X-Mimecast-Spam-Score",
    # OVH
    "X-Ovh-Spam-Reason",
    "X-VR-SpamCause",
    # Generic
    "X-Spam",
    "X-IP-Spam-Verdict",
    # Authentication
    "Authentication-Results",
]


def extract_spam_headers(message) -> dict[str, str]:
    """Extract spam-related headers from an email message.

    Args:
        message: A mailbox.Message object

    Returns:
        Dict of header name -> value for spam-related headers
    """
    headers = {}
    for header_name in SPAM_HEADERS:
        value = message.get(header_name)
        if value:
            # Decode if needed and strip whitespace
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="replace")
                except Exception:
                    value = str(value)
            headers[header_name] = str(value).strip()
    return headers


@dataclass
class ThunderbirdEmail:
    """Email read from Thunderbird mbox cache."""
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str  # For LLM classification
    mbox_path: str  # For later retrieval of raw email
    headers: dict[str, str] | None = None  # Spam-related headers for filtering


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
                import configparser
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


def list_mbox_files(mail_dir: Path) -> list[tuple[str, Path]]:
    """List all mbox files in a mail directory, returning (folder_name, path) tuples."""
    mbox_files = []

    for item in mail_dir.rglob("*"):
        # mbox files have no extension, skip .msf (index) and .dat files
        if item.is_file() and not item.suffix and not item.name.startswith("."):
            # Check if it looks like an mbox file (has corresponding .msf or is non-empty)
            msf_file = item.with_suffix(".msf")
            if msf_file.exists() or item.stat().st_size > 0:
                # Derive folder name from path relative to mail_dir
                rel_path = item.relative_to(mail_dir)
                # Handle .sbd subdirectories (Thunderbird's subfolder convention)
                parts = []
                for part in rel_path.parts:
                    if part.endswith(".sbd"):
                        parts.append(part[:-4])  # Remove .sbd suffix
                    else:
                        parts.append(part)
                folder_name = "/".join(parts)
                mbox_files.append((folder_name, item))

    return mbox_files


def _open_mbox(mbox_path: Path) -> mailbox.mbox | None:
    """Open an mbox file with error handling."""
    try:
        return mailbox.mbox(mbox_path)
    except PermissionError as e:
        logger.warning(f"Permission denied opening mbox {mbox_path}: {e}")
    except FileNotFoundError as e:
        logger.warning(f"Mbox file not found {mbox_path}: {e}")
    except Exception as e:
        logger.error(f"Failed to open mbox {mbox_path}: {e}")
    return None


def _parse_message(
    message, folder_name: str, mbox_path_str: str
) -> ThunderbirdEmail | None:
    """Parse a mailbox message into ThunderbirdEmail."""
    try:
        message_id = message.get("Message-ID", f"<tb-{hash(str(message))}@local>")
        subject = decode_mime_header(message.get("Subject"))
        from_addr = decode_mime_header(message.get("From"))
        body = extract_body(message)
        headers = extract_spam_headers(message)

        return ThunderbirdEmail(
            message_id=message_id,
            folder=folder_name,
            subject=subject,
            from_addr=from_addr,
            body_text=body,
            mbox_path=mbox_path_str,
            headers=headers if headers else None,
        )
    except (UnicodeDecodeError, LookupError) as e:
        logger.debug(f"Encoding error parsing email in {folder_name}: {e}")
    except Exception as e:
        logger.warning(f"Failed to parse email in {folder_name}: {e}")
    return None


def read_mbox(mbox_path: Path, folder_name: str, limit: int | None = None) -> Iterator[ThunderbirdEmail]:
    """Read emails from an mbox file.

    Args:
        mbox_path: Path to the mbox file
        folder_name: Name to assign to the folder
        limit: Maximum number of emails to read (None for all)

    Yields:
        ThunderbirdEmail objects for each successfully parsed email
    """
    mbox = _open_mbox(mbox_path)
    if mbox is None:
        return

    mbox_path_str = str(mbox_path)
    count = 0
    for message in mbox:
        if limit and count >= limit:
            break

        email = _parse_message(message, folder_name, mbox_path_str)
        if email:
            yield email
            count += 1

    mbox.close()


def read_mbox_random(
    mbox_path: Path,
    folder_name: str,
    limit: int | float,
) -> Iterator[ThunderbirdEmail]:
    """Read a random sample of emails from an mbox file.

    Args:
        mbox_path: Path to the mbox file
        folder_name: Name to assign to the folder
        limit: Number of emails (int >= 1) or fraction to sample (float 0-1)

    Yields:
        ThunderbirdEmail objects for each successfully parsed email
    """
    import random

    mbox = _open_mbox(mbox_path)
    if mbox is None:
        return

    try:
        keys = list(mbox.keys())
        total = len(keys)

        if total == 0:
            logger.info(f"No emails in {folder_name}")
            return

        # Calculate sample size: if < 1, treat as percentage; otherwise as count
        if isinstance(limit, float) and limit < 1:
            sample_size = max(1, int(total * limit))
            logger.info(f"Randomly sampling {limit:.0%} ({sample_size} of {total}) emails from {folder_name}")
        else:
            sample_size = min(int(limit), total)
            logger.info(f"Randomly sampling {sample_size} of {total} emails from {folder_name}")

        sampled_keys = random.sample(keys, sample_size)
        mbox_path_str = str(mbox_path)
        yielded = 0

        for key in sampled_keys:
            email = _parse_message(mbox[key], folder_name, mbox_path_str)
            if email:
                yield email
                yielded += 1

        logger.info(f"Successfully read {yielded} emails from {folder_name}")
    finally:
        mbox.close()


def get_raw_email(mbox_path: str, message_id: str) -> bytes | None:
    """Retrieve the raw email content from an mbox file by message_id.

    Args:
        mbox_path: Path to the mbox file (must be within ImapMail or Mail directory)
        message_id: Message-ID header to search for

    Returns:
        Raw email bytes if found, None otherwise
    """
    # Validate path is within expected Thunderbird directories
    path = Path(mbox_path).resolve()
    path_str = str(path)
    if "/ImapMail/" not in path_str and "/Mail/" not in path_str:
        logger.error(f"Rejected mbox path outside Thunderbird directories: {mbox_path}")
        return None

    try:
        mbox = mailbox.mbox(mbox_path)
    except Exception as e:
        logger.error(f"Failed to open mbox {mbox_path}: {e}")
        return None

    try:
        for message in mbox:
            if message.get("Message-ID") == message_id:
                return message.as_bytes()
    except Exception as e:
        logger.error(f"Error reading mbox {mbox_path}: {e}")
    finally:
        mbox.close()

    return None


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
    import re

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


class ThunderbirdReader:
    """Read emails from a Thunderbird profile's IMAP cache."""

    def __init__(self, profile_path: Path | None = None, server_filter: str | None = None):
        """
        Initialize the reader.

        Args:
            profile_path: Path to Thunderbird profile, or None to auto-detect
            server_filter: Optional IMAP server name to filter (e.g., "imap.gmail.com")
        """
        self.profile_path = find_thunderbird_profile(profile_path)
        self.server_filter = server_filter
        self._account_mapping: dict[str, str] | None = None

        if not self.profile_path:
            raise ValueError("Could not find Thunderbird profile")

        if not self.profile_path.exists():
            raise ValueError(f"Thunderbird profile not found: {self.profile_path}")

    def get_account_mapping(self) -> dict[str, str]:
        """Get mapping of server hostnames to Thunderbird account IDs.

        Returns:
            Dict of server hostname -> account ID
            Includes "local" key for Local Folders account
        """
        if self._account_mapping is None:
            assert self.profile_path is not None
            self._account_mapping = get_account_server_mapping(self.profile_path)
        return self._account_mapping

    def resolve_server_to_account_id(self, server_name: str) -> str:
        """Resolve a server hostname to a Thunderbird account ID.

        Args:
            server_name: Server hostname (e.g., "outlook.office365.com") or "local"

        Returns:
            Thunderbird account ID (e.g., "account1")

        Raises:
            ValueError: If server not found in profile
        """
        mapping = self.get_account_mapping()

        if server_name in mapping:
            return mapping[server_name]

        # List available servers for error message
        available = [s for s in mapping if s != "local"]
        if available:
            raise ValueError(
                f"Server '{server_name}' not found in Thunderbird profile.\n"
                f"Available servers: {', '.join(sorted(available))}"
            )
        else:
            raise ValueError(
                f"Server '{server_name}' not found in Thunderbird profile.\n"
                "No IMAP accounts configured."
            )

    def list_servers(self) -> list[str]:
        """List available IMAP servers in the profile."""
        assert self.profile_path is not None  # Validated in __init__
        imap_dirs = find_imap_mail_dirs(self.profile_path)
        return [d.name for d in imap_dirs]

    def resolve_folder(self, folder_spec: str) -> tuple[str, str]:
        """Resolve a folder specification to (server, folder_name).

        Args:
            folder_spec: Either "folder_name" or "server:folder_name"

        Returns:
            Tuple of (server_name, folder_name)

        Raises:
            ValueError: If folder not found or ambiguous (multiple servers)
        """
        assert self.profile_path is not None

        # Parse server:folder syntax
        if ":" in folder_spec:
            server, folder_name = folder_spec.split(":", 1)
        else:
            server = None
            folder_name = folder_spec

        # Find all matching folders
        imap_dirs = find_imap_mail_dirs(self.profile_path)
        if self.server_filter:
            imap_dirs = [d for d in imap_dirs if d.name == self.server_filter]
        if server:
            imap_dirs = [d for d in imap_dirs if d.name == server]

        matches = []
        for imap_dir in imap_dirs:
            for name, path in list_mbox_files(imap_dir):
                if name == folder_name:
                    matches.append((imap_dir.name, folder_name, path))

        if not matches:
            if server:
                raise ValueError(f"Folder '{folder_name}' not found in server '{server}'")
            else:
                raise ValueError(f"Folder '{folder_name}' not found")

        if len(matches) > 1:
            servers = [m[0] for m in matches]
            raise ValueError(
                f"Folder '{folder_name}' found in multiple accounts: {', '.join(servers)}. "
                f"Use server:folder syntax (e.g., '{servers[0]}:{folder_name}')"
            )

        return matches[0][0], matches[0][1]

    def list_folders(self, server: str | None = None) -> list[str]:
        """List all folders for a server (or all servers if not specified)."""
        assert self.profile_path is not None  # Validated in __init__
        imap_dirs = find_imap_mail_dirs(self.profile_path)

        if server:
            imap_dirs = [d for d in imap_dirs if d.name == server]
        elif self.server_filter:
            imap_dirs = [d for d in imap_dirs if d.name == self.server_filter]

        folders = []
        for imap_dir in imap_dirs:
            for folder_name, _ in list_mbox_files(imap_dir):
                folders.append(folder_name)

        return folders

    def list_folders_qualified(self) -> list[str]:
        """List all folders with server prefix (server:folder format).

        Returns folders in server:folder format to avoid ambiguity when
        the same folder name exists in multiple accounts.
        """
        assert self.profile_path is not None
        imap_dirs = find_imap_mail_dirs(self.profile_path)

        if self.server_filter:
            imap_dirs = [d for d in imap_dirs if d.name == self.server_filter]

        folders = []
        for imap_dir in imap_dirs:
            server = imap_dir.name
            for folder_name, _ in list_mbox_files(imap_dir):
                folders.append(f"{server}:{folder_name}")

        return folders

    def read_folder(
        self,
        folder_spec: str,
        limit: int | None = None,
    ) -> Iterator[ThunderbirdEmail]:
        """Read emails from a specific folder.

        Args:
            folder_spec: Folder name or "server:folder" for disambiguation
            limit: Maximum number of emails to read

        Raises:
            ValueError: If folder not found or ambiguous
        """
        assert self.profile_path is not None
        server, folder_name = self.resolve_folder(folder_spec)

        imap_dir = self.profile_path / "ImapMail" / server
        for name, path in list_mbox_files(imap_dir):
            if name == folder_name:
                yield from read_mbox(path, folder_name, limit)
                return

    def read_all(
        self,
        server: str | None = None,
        limit_per_folder: int | None = None,
    ) -> Iterator[ThunderbirdEmail]:
        """Read all emails from all folders."""
        assert self.profile_path is not None  # Validated in __init__
        imap_dirs = find_imap_mail_dirs(self.profile_path)

        if server:
            imap_dirs = [d for d in imap_dirs if d.name == server]
        elif self.server_filter:
            imap_dirs = [d for d in imap_dirs if d.name == self.server_filter]

        for imap_dir in imap_dirs:
            for folder_name, path in list_mbox_files(imap_dir):
                yield from read_mbox(path, folder_name, limit_per_folder)

    def get_sample_emails(
        self,
        folder_spec: str,
        count: int = 10,
    ) -> list[ThunderbirdEmail]:
        """Get sample emails from a folder for description generation."""
        return list(self.read_folder(folder_spec, limit=count))

    def read_folder_random(
        self,
        folder_spec: str,
        limit: int | float,
    ) -> Iterator[ThunderbirdEmail]:
        """Read a random sample of emails from a specific folder.

        Args:
            folder_spec: Folder name or "server:folder" for disambiguation
            limit: Number of emails (int >= 1) or fraction to sample (float 0-1)

        Raises:
            ValueError: If folder not found or ambiguous
        """
        assert self.profile_path is not None
        server, folder_name = self.resolve_folder(folder_spec)

        imap_dir = self.profile_path / "ImapMail" / server
        for name, path in list_mbox_files(imap_dir):
            if name == folder_name:
                yield from read_mbox_random(path, folder_name, limit)
                return
