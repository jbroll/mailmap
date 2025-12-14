"""Tests for Thunderbird module."""

import mailbox

import pytest

from mailmap.mbox import ThunderbirdEmail, list_mbox_files, read_mbox
from mailmap.profile import (
    find_imap_mail_dirs,
    get_account_server_mapping,
    parse_prefs_js,
)
from mailmap.thunderbird import ThunderbirdReader


@pytest.fixture
def mock_thunderbird_profile(temp_dir):
    """Create a mock Thunderbird profile structure."""
    profile = temp_dir / "mock.default"
    profile.mkdir()

    # Create ImapMail directory with a mock server
    imap_mail = profile / "ImapMail" / "imap.example.com"
    imap_mail.mkdir(parents=True)

    # Create mock mbox files
    inbox_mbox = imap_mail / "INBOX"
    sent_mbox = imap_mail / "Sent"

    # Create a simple mbox with test messages
    mbox = mailbox.mbox(inbox_mbox)
    msg1 = mailbox.mboxMessage()
    msg1["Message-ID"] = "<test1@example.com>"
    msg1["From"] = "sender@example.com"
    msg1["Subject"] = "Test Subject 1"
    msg1.set_payload("This is the body of test email 1.")
    mbox.add(msg1)

    msg2 = mailbox.mboxMessage()
    msg2["Message-ID"] = "<test2@example.com>"
    msg2["From"] = "another@example.com"
    msg2["Subject"] = "Test Subject 2"
    msg2.set_payload("This is the body of test email 2.")
    mbox.add(msg2)
    mbox.close()

    # Create empty Sent mbox
    sent_mbox.touch()

    # Create .msf files (index files that Thunderbird creates)
    (imap_mail / "INBOX.msf").touch()
    (imap_mail / "Sent.msf").touch()

    # Create Local Folders directory
    local_folders = profile / "Mail" / "Local Folders"
    local_folders.mkdir(parents=True)

    # Create prefs.js with account mappings
    prefs_js = profile / "prefs.js"
    prefs_js.write_text(f'''// Mozilla User Preferences
user_pref("mail.account.account1.server", "server1");
user_pref("mail.account.account2.server", "server2");
user_pref("mail.accountmanager.accounts", "account1,account2");
user_pref("mail.accountmanager.localfoldersserver", "server2");
user_pref("mail.server.server1.directory", "{imap_mail}");
user_pref("mail.server.server1.hostname", "imap.example.com");
user_pref("mail.server.server1.type", "imap");
user_pref("mail.server.server2.directory", "{local_folders}");
user_pref("mail.server.server2.type", "none");
''')

    return profile


@pytest.fixture
def mock_profile_with_subfolders(temp_dir):
    """Create a mock profile with nested subfolders."""
    profile = temp_dir / "nested.default"
    profile.mkdir()

    imap_mail = profile / "ImapMail" / "imap.test.com"
    imap_mail.mkdir(parents=True)

    # Create INBOX
    inbox = imap_mail / "INBOX"
    inbox.touch()
    (imap_mail / "INBOX.msf").touch()

    # Create subfolder using .sbd convention
    inbox_sbd = imap_mail / "INBOX.sbd"
    inbox_sbd.mkdir()

    work = inbox_sbd / "Work"
    work.touch()
    (inbox_sbd / "Work.msf").touch()

    return profile


@pytest.fixture
def mock_profile_multiple_accounts(temp_dir):
    """Create a mock profile with multiple IMAP accounts having the same folder."""
    profile = temp_dir / "multi.default"
    profile.mkdir()

    # First account
    imap1 = profile / "ImapMail" / "imap.gmail.com"
    imap1.mkdir(parents=True)
    (imap1 / "INBOX").touch()
    (imap1 / "INBOX.msf").touch()

    # Second account
    imap2 = profile / "ImapMail" / "outlook.office365.com"
    imap2.mkdir(parents=True)
    (imap2 / "INBOX").touch()
    (imap2 / "INBOX.msf").touch()
    (imap2 / "Drafts").touch()
    (imap2 / "Drafts.msf").touch()

    return profile


class TestThunderbirdEmail:
    def test_dataclass(self):
        email = ThunderbirdEmail(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="Email body",
            mbox_path="/path/to/mbox",
        )
        assert email.message_id == "<test@example.com>"
        assert email.folder == "INBOX"
        assert email.subject == "Test Subject"
        assert email.mbox_path == "/path/to/mbox"


class TestFindImapMailDirs:
    def test_finds_imap_dirs(self, mock_thunderbird_profile):
        imap_dirs = find_imap_mail_dirs(mock_thunderbird_profile)
        assert len(imap_dirs) == 1
        assert imap_dirs[0].name == "imap.example.com"

    def test_no_imap_dir(self, temp_dir):
        profile = temp_dir / "empty.default"
        profile.mkdir()
        imap_dirs = find_imap_mail_dirs(profile)
        assert imap_dirs == []


class TestListMboxFiles:
    def test_lists_mbox_files(self, mock_thunderbird_profile):
        imap_dir = mock_thunderbird_profile / "ImapMail" / "imap.example.com"
        mbox_files = list_mbox_files(imap_dir)

        folder_names = {name for name, _ in mbox_files}
        assert "INBOX" in folder_names
        assert "Sent" in folder_names

    def test_handles_subfolders(self, mock_profile_with_subfolders):
        imap_dir = mock_profile_with_subfolders / "ImapMail" / "imap.test.com"
        mbox_files = list_mbox_files(imap_dir)

        folder_names = {name for name, _ in mbox_files}
        assert "INBOX" in folder_names
        assert "INBOX/Work" in folder_names


class TestReadMbox:
    def test_reads_emails(self, mock_thunderbird_profile):
        mbox_path = mock_thunderbird_profile / "ImapMail" / "imap.example.com" / "INBOX"
        emails = list(read_mbox(mbox_path, "INBOX"))

        assert len(emails) == 2

        subjects = {e.subject for e in emails}
        assert "Test Subject 1" in subjects
        assert "Test Subject 2" in subjects

    def test_respects_limit(self, mock_thunderbird_profile):
        mbox_path = mock_thunderbird_profile / "ImapMail" / "imap.example.com" / "INBOX"
        emails = list(read_mbox(mbox_path, "INBOX", limit=1))

        assert len(emails) == 1

    def test_empty_mbox(self, mock_thunderbird_profile):
        mbox_path = mock_thunderbird_profile / "ImapMail" / "imap.example.com" / "Sent"
        emails = list(read_mbox(mbox_path, "Sent"))

        assert len(emails) == 0


class TestThunderbirdReader:
    def test_init_with_explicit_path(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        assert reader.profile_path == mock_thunderbird_profile

    def test_init_invalid_path(self, temp_dir):
        with pytest.raises(ValueError, match="Could not find Thunderbird profile"):
            ThunderbirdReader(profile_path=temp_dir / "nonexistent")

    def test_list_servers(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        servers = reader.list_servers()

        assert "imap.example.com" in servers

    def test_list_folders(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        folders = reader.list_folders()

        assert "INBOX" in folders
        assert "Sent" in folders

    def test_read_folder(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        emails = list(reader.read_folder("INBOX"))

        assert len(emails) == 2
        assert all(isinstance(e, ThunderbirdEmail) for e in emails)

    def test_read_folder_with_server_prefix(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        emails = list(reader.read_folder("imap.example.com:INBOX"))

        assert len(emails) == 2

    def test_read_folder_with_limit(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        emails = list(reader.read_folder("INBOX", limit=1))

        assert len(emails) == 1

    def test_get_sample_emails(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        samples = reader.get_sample_emails("INBOX", count=5)

        assert len(samples) == 2  # Only 2 emails in mock
        assert all(isinstance(e, ThunderbirdEmail) for e in samples)

    def test_resolve_folder(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        server, folder = reader.resolve_folder("INBOX")

        assert server == "imap.example.com"
        assert folder == "INBOX"

    def test_resolve_folder_with_prefix(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        server, folder = reader.resolve_folder("imap.example.com:Sent")

        assert server == "imap.example.com"
        assert folder == "Sent"

    def test_resolve_folder_not_found(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        with pytest.raises(ValueError, match="Folder 'Nonexistent' not found"):
            reader.resolve_folder("Nonexistent")

    def test_list_folders_qualified(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        folders = reader.list_folders_qualified()

        assert "imap.example.com:INBOX" in folders
        assert "imap.example.com:Sent" in folders

    def test_server_filter(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(
            profile_path=mock_thunderbird_profile,
            server_filter="imap.example.com",
        )
        folders = reader.list_folders()

        assert "INBOX" in folders

    def test_server_filter_no_match(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(
            profile_path=mock_thunderbird_profile,
            server_filter="nonexistent.server.com",
        )
        folders = reader.list_folders()

        assert folders == []

    def test_read_all(self, mock_thunderbird_profile):
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        emails = list(reader.read_all())

        # 2 emails in INBOX, 0 in Sent
        assert len(emails) == 2

    def test_resolve_folder_ambiguous(self, mock_profile_multiple_accounts):
        """Test that ambiguous folder names raise an error."""
        reader = ThunderbirdReader(profile_path=mock_profile_multiple_accounts)
        with pytest.raises(ValueError, match="found in multiple accounts"):
            reader.resolve_folder("INBOX")

    def test_resolve_folder_ambiguous_with_prefix(self, mock_profile_multiple_accounts):
        """Test that server:folder syntax resolves ambiguity."""
        reader = ThunderbirdReader(profile_path=mock_profile_multiple_accounts)
        server, folder = reader.resolve_folder("imap.gmail.com:INBOX")

        assert server == "imap.gmail.com"
        assert folder == "INBOX"

    def test_resolve_unique_folder(self, mock_profile_multiple_accounts):
        """Test that unique folder names work without prefix."""
        reader = ThunderbirdReader(profile_path=mock_profile_multiple_accounts)
        server, folder = reader.resolve_folder("Drafts")

        assert server == "outlook.office365.com"
        assert folder == "Drafts"

    def test_list_folders_qualified_multiple_accounts(self, mock_profile_multiple_accounts):
        """Test qualified folder listing with multiple accounts."""
        reader = ThunderbirdReader(profile_path=mock_profile_multiple_accounts)
        folders = reader.list_folders_qualified()

        assert "imap.gmail.com:INBOX" in folders
        assert "outlook.office365.com:INBOX" in folders
        assert "outlook.office365.com:Drafts" in folders
        assert len(folders) == 3

    def test_resolve_server_to_account_id(self, mock_thunderbird_profile):
        """Test resolving server hostname to account ID."""
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        account_id = reader.resolve_server_to_account_id("imap.example.com")

        assert account_id == "account1"

    def test_resolve_server_to_account_id_local(self, mock_thunderbird_profile):
        """Test resolving 'local' to Local Folders account ID."""
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        account_id = reader.resolve_server_to_account_id("local")

        assert account_id == "account2"

    def test_resolve_server_to_account_id_not_found(self, mock_thunderbird_profile):
        """Test that unknown server raises ValueError."""
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        with pytest.raises(ValueError, match="not found in Thunderbird profile"):
            reader.resolve_server_to_account_id("unknown.server.com")

    def test_get_account_mapping(self, mock_thunderbird_profile):
        """Test getting the server to account ID mapping."""
        reader = ThunderbirdReader(profile_path=mock_thunderbird_profile)
        mapping = reader.get_account_mapping()

        assert "imap.example.com" in mapping
        assert "local" in mapping
        assert mapping["imap.example.com"] == "account1"
        assert mapping["local"] == "account2"


class TestParsePrefsJs:
    def test_parse_prefs_js(self, mock_thunderbird_profile):
        """Test parsing prefs.js into a dictionary."""
        prefs = parse_prefs_js(mock_thunderbird_profile)

        assert prefs["mail.account.account1.server"] == "server1"
        assert prefs["mail.account.account2.server"] == "server2"
        assert prefs["mail.server.server1.hostname"] == "imap.example.com"
        assert prefs["mail.server.server1.type"] == "imap"

    def test_parse_prefs_js_missing_file(self, temp_dir):
        """Test parsing returns empty dict for missing prefs.js."""
        prefs = parse_prefs_js(temp_dir)
        assert prefs == {}


class TestGetAccountServerMapping:
    def test_get_mapping(self, mock_thunderbird_profile):
        """Test getting server hostname to account ID mapping."""
        mapping = get_account_server_mapping(mock_thunderbird_profile)

        assert "imap.example.com" in mapping
        assert "local" in mapping
        assert mapping["imap.example.com"] == "account1"
        assert mapping["local"] == "account2"

    def test_get_mapping_no_prefs(self, temp_dir):
        """Test mapping returns empty dict when no prefs.js exists."""
        mapping = get_account_server_mapping(temp_dir)
        assert mapping == {}
