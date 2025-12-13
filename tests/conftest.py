"""Shared test fixtures."""

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
def sample_config_toml(temp_dir):
    """Create a sample TOML config file."""
    config_path = temp_dir / "config.toml"
    config_path.write_text('''
[imap]
host = "imap.test.com"
port = 993
username = "user@test.com"
password = "secret"
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
