"""Configuration management for mailmap."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImapConfig:
    """IMAP server configuration.

    Credentials can be provided via environment variables:
    - MAILMAP_IMAP_USERNAME: IMAP username (overrides config file)
    - MAILMAP_IMAP_PASSWORD: IMAP password (overrides config file)
    """
    host: str
    port: int = 993
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    idle_folders: list[str] = field(default_factory=lambda: ["INBOX"])
    poll_interval_seconds: int = 300

    def __post_init__(self):
        """Apply environment variable overrides for credentials."""
        env_username = os.environ.get("MAILMAP_IMAP_USERNAME")
        env_password = os.environ.get("MAILMAP_IMAP_PASSWORD")

        if env_username:
            self.username = env_username
        if env_password:
            self.password = env_password


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout_seconds: int = 300  # 5 minutes for large batches


@dataclass
class DatabaseConfig:
    path: str = "mailmap.db"


@dataclass
class ThunderbirdConfig:
    profile_path: str | None = None  # Auto-detect if not specified
    server_filter: str | None = None  # Filter to specific IMAP server
    samples_per_folder: int = 20  # Number of emails to sample for descriptions
    import_limit: int | None = None  # Max emails to import per folder (None = all)
    init_sample_limit: int = 100  # Max emails to sample for --init-folders


@dataclass
class Config:
    imap: ImapConfig
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    thunderbird: ThunderbirdConfig = field(default_factory=ThunderbirdConfig)


def load_config(path: str | Path) -> Config:
    """Load configuration from a TOML file."""
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)

    imap_data = data.get("imap", {})
    imap_config = ImapConfig(
        host=imap_data.get("host", ""),
        port=imap_data.get("port", 993),
        username=imap_data.get("username", ""),
        password=imap_data.get("password", ""),
        use_ssl=imap_data.get("use_ssl", True),
        idle_folders=imap_data.get("idle_folders", ["INBOX"]),
        poll_interval_seconds=imap_data.get("poll_interval_seconds", 300),
    )

    ollama_data = data.get("ollama", {})
    ollama_config = OllamaConfig(
        base_url=ollama_data.get("base_url", "http://localhost:11434"),
        model=ollama_data.get("model", "qwen2.5:7b"),
        timeout_seconds=ollama_data.get("timeout_seconds", 120),
    )

    db_data = data.get("database", {})
    db_config = DatabaseConfig(
        path=db_data.get("path", "mailmap.db"),
    )

    tb_data = data.get("thunderbird", {})
    tb_config = ThunderbirdConfig(
        profile_path=tb_data.get("profile_path"),
        server_filter=tb_data.get("server_filter"),
        samples_per_folder=tb_data.get("samples_per_folder", 20),
        import_limit=tb_data.get("import_limit"),
        init_sample_limit=tb_data.get("init_sample_limit", 100),
    )

    return Config(
        imap=imap_config,
        ollama=ollama_config,
        database=db_config,
        thunderbird=tb_config,
    )
