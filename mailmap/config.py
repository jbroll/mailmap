"""Configuration management for mailmap."""

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ImapConfig:
    """IMAP server configuration.

    Credentials MUST be provided via environment variables:
    - MAILMAP_IMAP_USERNAME: IMAP username
    - MAILMAP_IMAP_PASSWORD: IMAP password
    """
    host: str
    port: int = 993
    username: str = ""
    password: str = field(default="", repr=False)
    use_ssl: bool = True
    idle_folders: list[str] = field(default_factory=lambda: ["INBOX"])
    poll_interval_seconds: int = 300

    def __post_init__(self):
        """Load credentials from environment variables."""
        env_username = os.environ.get("MAILMAP_IMAP_USERNAME")
        env_password = os.environ.get("MAILMAP_IMAP_PASSWORD")

        if env_username:
            self.username = env_username
        if env_password:
            self.password = env_password


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:14b"
    timeout_seconds: int = 300  # 5 minutes for large batches


@dataclass
class DatabaseConfig:
    path: str = "mailmap.db"
    categories_file: str = "categories.txt"


@dataclass
class ThunderbirdConfig:
    profile_path: str | None = None  # Auto-detect if not specified
    server_filter: str | None = None  # Filter to specific IMAP server
    folder_filter: str | None = None  # Filter to specific folder (e.g., INBOX)
    samples_per_folder: int = 20  # Number of emails to sample for descriptions
    import_limit: int | float | None = None  # Max emails: int=count, float(0-1)=percentage
    init_sample_limit: int | float = 100  # Max emails: int=count, float(0-1)=percentage
    random_sample: bool = False  # Use random sampling instead of sequential
    source_type: str | None = None  # Force source: 'thunderbird' or 'imap'


@dataclass
class WebSocketConfig:
    """WebSocket server configuration for Thunderbird MailExtension communication.

    Authentication token can be set via MAILMAP_WS_TOKEN environment variable.
    """
    enabled: bool = False
    host: str = "127.0.0.1"  # Localhost only for security
    port: int = 9753
    auth_token: str = field(default="", repr=False)

    def __post_init__(self):
        """Load auth token from environment."""
        env_token = os.environ.get("MAILMAP_WS_TOKEN")
        if env_token:
            self.auth_token = env_token


# Default spam rules covering common spam filters
DEFAULT_SPAM_RULES = [
    # Microsoft/Office 365
    "X-MS-Exchange-Organization-SCL >= 5",
    "X-Microsoft-Antispam /BCL:(\\d+)/ >= 7",
    # SpamAssassin
    "X-Spam-Flag == YES",
    "X-Spam-Status prefix Yes",
    "X-Spam-Score >= 5.0",
    # Rspamd
    "X-Rspamd-Action in reject|add header|greylist",
    "X-Rspamd-Score >= 6.0",
    # Barracuda
    "X-Barracuda-Spam-Status == Yes",
    "X-Barracuda-Spam-Score >= 3.5",
    # SpamExperts / Spampanel
    "X-SpamExperts-Class == spam",
    "X-SpamExperts-Outgoing-Class == spam",
    "X-Spampanel-Outgoing-Class == spam",
    # Proofpoint
    "X-Proofpoint-Spam-Details contains rule=spam",
    # Cisco IronPort
    "X-IronPort-Anti-Spam-Result contains spam",
    # Trend Micro
    "X-TM-AS-Result == spam",
    "X-TMASE-Result == spam",
    # Mimecast
    "X-Mimecast-Spam-Score >= 4",
    # OVH
    "X-Ovh-Spam-Reason exists",
    "X-VR-SpamCause exists",
    # Generic
    "X-Spam == Yes",
    "X-IP-Spam-Verdict == spam",
]


@dataclass
class SpamConfig:
    """Spam detection configuration.

    Rules use a DSL format: HEADER [/REGEX/] OPERATOR VALUE

    Examples:
        X-MS-Exchange-Organization-SCL >= 5
        X-Spam-Flag == YES
        X-Microsoft-Antispam /BCL:(\\d+)/ >= 7
        X-Rspamd-Action in reject|add header|greylist
        X-Ovh-Spam-Reason exists
    """
    enabled: bool = True
    skip_folders: list[str] = field(default_factory=lambda: [
        "Junk", "Spam", "Deleted", "Deleted Items", "Trash"
    ])
    rules: list[str] = field(default_factory=lambda: DEFAULT_SPAM_RULES.copy())


@dataclass
class Config:
    imap: ImapConfig
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    thunderbird: ThunderbirdConfig = field(default_factory=ThunderbirdConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    spam: SpamConfig = field(default_factory=SpamConfig)


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
        categories_file=db_data.get("categories_file", "categories.txt"),
    )

    tb_data = data.get("thunderbird", {})
    tb_config = ThunderbirdConfig(
        profile_path=tb_data.get("profile_path"),
        server_filter=tb_data.get("server_filter"),
        folder_filter=tb_data.get("folder_filter"),
        samples_per_folder=tb_data.get("samples_per_folder", 20),
        import_limit=tb_data.get("import_limit"),
        init_sample_limit=tb_data.get("init_sample_limit", 100),
        random_sample=tb_data.get("random_sample", False),
    )

    ws_data = data.get("websocket", {})
    ws_config = WebSocketConfig(
        enabled=ws_data.get("enabled", False),
        host=ws_data.get("host", "127.0.0.1"),
        port=ws_data.get("port", 9753),
    )

    spam_data = data.get("spam", {})
    spam_config = SpamConfig(
        enabled=spam_data.get("enabled", True),
        skip_folders=spam_data.get("skip_folders", [
            "Junk", "Spam", "Deleted", "Deleted Items", "Trash"
        ]),
        rules=spam_data.get("rules", DEFAULT_SPAM_RULES.copy()),
    )

    return Config(
        imap=imap_config,
        ollama=ollama_config,
        database=db_config,
        thunderbird=tb_config,
        websocket=ws_config,
        spam=spam_config,
    )
