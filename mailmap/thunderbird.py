"""Thunderbird profile reader for importing existing emails."""

from collections.abc import Iterator
from pathlib import Path

from .mbox import (
    ThunderbirdEmail,
    get_raw_email,
    list_mbox_files,
    read_mbox,
    read_mbox_random,
)
from .profile import (
    find_imap_mail_dirs,
    find_thunderbird_profile,
    get_account_server_mapping,
)

# Re-export for external use
__all__ = [
    "ThunderbirdEmail",
    "ThunderbirdReader",
    "find_thunderbird_profile",
    "get_raw_email",
]


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
