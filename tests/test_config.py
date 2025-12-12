"""Tests for config module."""

import pytest

from mailmap.config import (
    load_config,
    Config,
    ImapConfig,
    OllamaConfig,
    DatabaseConfig,
    ThunderbirdConfig,
)


class TestImapConfig:
    def test_defaults(self):
        config = ImapConfig(host="imap.example.com")
        assert config.port == 993
        assert config.use_ssl is True
        assert config.idle_folders == ["INBOX"]
        assert config.poll_interval_seconds == 300

    def test_custom_values(self):
        config = ImapConfig(
            host="mail.test.com",
            port=143,
            username="user",
            password="pass",
            use_ssl=False,
            idle_folders=["INBOX", "Sent"],
            poll_interval_seconds=60,
        )
        assert config.host == "mail.test.com"
        assert config.port == 143
        assert config.use_ssl is False


class TestOllamaConfig:
    def test_defaults(self):
        config = OllamaConfig()
        assert config.base_url == "http://localhost:11434"
        assert config.model == "qwen2.5:7b"
        assert config.timeout_seconds == 120


class TestDatabaseConfig:
    def test_defaults(self):
        config = DatabaseConfig()
        assert config.path == "mailmap.db"


class TestThunderbirdConfig:
    def test_defaults(self):
        config = ThunderbirdConfig()
        assert config.profile_path is None
        assert config.server_filter is None
        assert config.samples_per_folder == 20
        assert config.import_limit is None


class TestLoadConfig:
    def test_load_config(self, sample_config_toml):
        config = load_config(sample_config_toml)

        assert config.imap.host == "imap.test.com"
        assert config.imap.port == 993
        assert config.imap.username == "user@test.com"
        assert config.imap.password == "secret"
        assert config.imap.idle_folders == ["INBOX", "Important"]
        assert config.imap.poll_interval_seconds == 120

        assert config.ollama.base_url == "http://localhost:11434"
        assert config.ollama.model == "llama3:8b"
        assert config.ollama.timeout_seconds == 60

        assert config.database.path == "test.db"

        assert config.thunderbird.samples_per_folder == 10

    def test_load_config_missing_file(self, temp_dir):
        with pytest.raises(FileNotFoundError):
            load_config(temp_dir / "nonexistent.toml")
