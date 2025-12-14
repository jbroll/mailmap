"""Integration tests for IMAP client.

These tests require real IMAP credentials set via environment variables:
- MAILMAP_IMAP_USERNAME
- MAILMAP_IMAP_PASSWORD

Run with: pytest tests/test_imap_integration.py -v
Skip with: pytest tests/ --ignore=tests/test_imap_integration.py
"""

import os

import pytest
from imapclient.exceptions import LoginError

from mailmap.config import ImapConfig
from mailmap.imap_client import ImapMailbox

# Skip all tests if credentials not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("MAILMAP_IMAP_PASSWORD"),
    reason="IMAP credentials not set (MAILMAP_IMAP_PASSWORD)",
)

# Test server configuration
IMAP_HOST = "imap.purelymail.com"
IMAP_PORT = 993


@pytest.fixture
def imap_config():
    """Create IMAP config from environment."""
    return ImapConfig(
        host=IMAP_HOST,
        port=IMAP_PORT,
        username=os.environ.get("MAILMAP_IMAP_USERNAME", ""),
        password=os.environ.get("MAILMAP_IMAP_PASSWORD", ""),
    )


@pytest.fixture
def imap_client(imap_config):
    """Create connected IMAP client."""
    client = ImapMailbox(imap_config)
    client.connect()
    yield client
    client.disconnect()


class TestImapConnection:
    def test_connect_disconnect(self, imap_config):
        """Test basic connection and disconnection."""
        client = ImapMailbox(imap_config)
        client.connect()
        assert client._client is not None
        client.disconnect()
        assert client._client is None

    def test_connect_invalid_credentials(self, monkeypatch):
        """Test connection with invalid credentials fails."""
        # Clear env vars so they don't override test values
        monkeypatch.delenv("MAILMAP_IMAP_PASSWORD", raising=False)
        monkeypatch.delenv("MAILMAP_IMAP_USERNAME", raising=False)

        config = ImapConfig(
            host=IMAP_HOST,
            port=IMAP_PORT,
            username="invalid@example.com",
            password="wrongpassword",
        )
        client = ImapMailbox(config)
        with pytest.raises(LoginError):
            client.connect()


class TestImapFolders:
    def test_list_folders(self, imap_client):
        """Test listing folders."""
        folders = imap_client.list_folders()
        assert isinstance(folders, list)
        assert len(folders) > 0
        assert "INBOX" in folders

    def test_select_folder(self, imap_client):
        """Test selecting a folder."""
        result = imap_client.select_folder("INBOX")
        assert isinstance(result, dict)
        assert b"EXISTS" in result

    def test_folder_exists(self, imap_client):
        """Test checking folder existence."""
        assert imap_client.folder_exists("INBOX") is True
        assert imap_client.folder_exists("NonexistentFolder12345") is False


class TestImapEmails:
    def test_fetch_recent_uids(self, imap_client):
        """Test fetching recent UIDs."""
        uids = imap_client.fetch_recent_uids("INBOX", limit=10)
        assert isinstance(uids, list)
        # May be empty if INBOX is empty

    def test_fetch_email(self, imap_client):
        """Test fetching a single email."""
        uids = imap_client.fetch_recent_uids("INBOX", limit=1)
        if uids:
            email = imap_client.fetch_email(uids[0], "INBOX")
            assert email is not None
            assert email.message_id
            assert email.folder == "INBOX"
            assert email.uid == uids[0]
        else:
            pytest.skip("No emails in INBOX to test")

    def test_get_new_uids_since(self, imap_client):
        """Test getting UIDs since a given UID."""
        uids = imap_client.fetch_recent_uids("INBOX", limit=5)
        if len(uids) >= 2:
            # Get UIDs after the first one
            new_uids = imap_client.get_new_uids_since("INBOX", uids[0])
            assert isinstance(new_uids, list)
            # Should include later UIDs
            for uid in uids[1:]:
                assert uid in new_uids
        else:
            pytest.skip("Not enough emails in INBOX to test")


class TestImapFolderOperations:
    def test_create_and_check_folder(self, imap_client):
        """Test creating a folder."""
        test_folder = "TestFolder-Mailmap-Integration"

        # Clean up if exists from previous test
        if imap_client.folder_exists(test_folder):
            imap_client.client.delete_folder(test_folder)

        # Create folder
        created = imap_client.create_folder(test_folder)
        assert created is True
        assert imap_client.folder_exists(test_folder)

        # Try creating again - should return False
        created_again = imap_client.create_folder(test_folder)
        assert created_again is False

        # Clean up
        imap_client.client.delete_folder(test_folder)

    def test_ensure_folder(self, imap_client):
        """Test ensure_folder creates if not exists."""
        test_folder = "TestFolder-Mailmap-Ensure"

        # Clean up if exists
        if imap_client.folder_exists(test_folder):
            imap_client.client.delete_folder(test_folder)

        # Ensure creates it
        imap_client.ensure_folder(test_folder)
        assert imap_client.folder_exists(test_folder)

        # Ensure again doesn't fail
        imap_client.ensure_folder(test_folder)
        assert imap_client.folder_exists(test_folder)

        # Clean up
        imap_client.client.delete_folder(test_folder)


class TestImapClassify:
    """Test classify command with --source-type imap."""

    def test_classify_from_imap(self, tmp_path):
        """Test classify command reads emails from IMAP and classifies them."""
        import subprocess

        # Create minimal config file (IMAP host/port only, credentials from env)
        config_file = tmp_path / "config.toml"
        config_file.write_text(f"""
[imap]
host = "{IMAP_HOST}"
port = {IMAP_PORT}

[ollama]
base_url = "http://localhost:11434"
model = "qwen2.5:7b"
""")

        # Create simple categories file
        categories_file = tmp_path / "categories.txt"
        categories_file.write_text("""Receipts: Purchase receipts and order confirmations.

Newsletters: Email newsletters and subscriptions.

Personal: Personal correspondence.
""")

        # Run classify with IMAP source
        result = subprocess.run(
            [
                "python", "-m", "mailmap.main", "classify",
                "-c", str(config_file),
                "--db-path", str(tmp_path / "test.db"),
                "--source-type", "imap",
                "--folder", "INBOX",
                "--limit", "2",
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "MAILMAP_IMAP_USERNAME": os.environ.get("MAILMAP_IMAP_USERNAME", ""),
                "MAILMAP_IMAP_PASSWORD": os.environ.get("MAILMAP_IMAP_PASSWORD", ""),
            },
            cwd=tmp_path,
            timeout=120,
        )

        # Check that the command ran (may skip if no emails or Ollama not running)
        # Success if:
        # - Exit code 0 (classified emails)
        # - Exit code 0 with "No emails" message
        # - Exit code 1 with Ollama connection error (acceptable in test env)
        output = result.stdout + result.stderr

        if "Connection refused" in output or "Ollama" in output.lower():
            pytest.skip("Ollama not running")

        if "No emails" in output:
            pytest.skip("No emails in INBOX to classify")

        # Command should complete without IMAP connection errors
        assert "IMAP connection error" not in output, f"IMAP error: {output}"
        assert "Authentication failed" not in output, f"Auth error: {output}"

        # If we got here and have emails, check for classification output
        if result.returncode == 0:
            assert "Classifying" in output or "classified" in output.lower() or "Processed" in output

    def test_classify_imap_folder_filter(self, tmp_path):
        """Test that --folder filter works with IMAP source."""
        import subprocess

        config_file = tmp_path / "config.toml"
        config_file.write_text(f"""
[imap]
host = "{IMAP_HOST}"
port = {IMAP_PORT}

[ollama]
base_url = "http://localhost:11434"
model = "qwen2.5:7b"
""")

        categories_file = tmp_path / "categories.txt"
        categories_file.write_text("Test: Test category.\n")

        # Test with non-existent folder
        result = subprocess.run(
            [
                "python", "-m", "mailmap.main", "classify",
                "-c", str(config_file),
                "--db-path", str(tmp_path / "test.db"),
                "--source-type", "imap",
                "--folder", "NonexistentFolder12345",
                "--limit", "1",
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "MAILMAP_IMAP_USERNAME": os.environ.get("MAILMAP_IMAP_USERNAME", ""),
                "MAILMAP_IMAP_PASSWORD": os.environ.get("MAILMAP_IMAP_PASSWORD", ""),
            },
            cwd=tmp_path,
            timeout=60,
        )

        output = result.stdout + result.stderr

        if "Connection refused" in output or "Ollama" in output.lower():
            pytest.skip("Ollama not running")

        # Should handle missing folder gracefully
        assert "IMAP connection error" not in output
