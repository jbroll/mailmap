"""Shared test fixtures."""

import mailbox
import tempfile
from pathlib import Path

import pytest

from mailmap.config import Config, DatabaseConfig, ImapConfig, OllamaConfig, ThunderbirdConfig
from mailmap.database import Database


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


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
def sample_config(temp_dir):
    """Create a sample configuration for testing."""
    return Config(
        imap=ImapConfig(
            host="imap.example.com",
            port=993,
            username="test@example.com",
            password="testpass",
        ),
        ollama=OllamaConfig(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
        ),
        database=DatabaseConfig(
            path=str(temp_dir / "test.db"),
        ),
        thunderbird=ThunderbirdConfig(),
    )


@pytest.fixture
def test_db(temp_dir):
    """Create a test database."""
    db = Database(temp_dir / "test.db")
    db.connect()
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def sample_config_toml(temp_dir, monkeypatch):
    """Create a sample TOML config file."""
    # Password must come from environment variable
    monkeypatch.setenv("MAILMAP_IMAP_PASSWORD", "secret")

    config_path = temp_dir / "config.toml"
    config_path.write_text('''
[imap]
host = "imap.test.com"
port = 993
username = "user@test.com"
use_ssl = true
idle_folders = ["INBOX", "Important"]
poll_interval_seconds = 120

[ollama]
base_url = "http://localhost:11434"
model = "llama3:8b"
timeout_seconds = 60

[database]
path = "test.db"

[thunderbird]
samples_per_folder = 10
''')
    return config_path
