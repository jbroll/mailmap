# CLAUDE.md

Developer guidance for Claude Code when working with this repository.

## Quick Start

```bash
source venv/bin/activate
pip install -e ".[dev]"

export MAILMAP_IMAP_USERNAME="your-email@example.com"
export MAILMAP_IMAP_PASSWORD="your-password"
```

## Testing

```bash
pytest                          # All tests
pytest tests/test_database.py   # Specific file
pytest -v                       # Verbose
```

## Deployment

```bash
../deploy.sh/deploy.sh update .   # Deploy code changes to server
../deploy.sh/deploy.sh init .     # Full initial deployment (includes infrastructure)
```

## Project Structure

```
mailmap/
├── cli.py              # Argument parsing, command dispatch
├── main.py             # Entry point
├── commands/           # Command implementations
│   ├── daemon.py       # IMAP IDLE listener, EmailProcessor
│   ├── classify.py     # Bulk classification
│   ├── learn.py        # Learn categories from Thunderbird
│   ├── init.py         # Suggest folder structure
│   ├── upload.py       # Upload to IMAP, cleanup
│   ├── imap_ops.py     # IMAP management commands
│   └── utils.py        # list, summary, clear, reset, sync
├── sources/            # Email source abstractions
│   ├── thunderbird.py  # ThunderbirdSource
│   └── imap.py         # ImapSource
├── targets/            # Email target abstractions
│   ├── base.py         # EmailTarget protocol
│   ├── websocket.py    # WebSocketTarget (self-contained)
│   └── imap.py         # ImapTarget
├── prompts/            # LLM prompt templates (editable .txt files)
├── config.py           # TOML config with dataclasses
├── database.py         # SQLite operations
├── imap_client.py      # ImapMailbox, ImapListener
├── llm.py              # Ollama REST client
├── categories.py       # categories.txt parsing
├── content.py          # Email body extraction/cleaning
├── spam.py             # Header-based spam detection
├── email.py            # UnifiedEmail dataclass
├── thunderbird.py      # Thunderbird profile detection
├── mbox.py             # Mbox file reading
├── profile.py          # Profile path utilities
├── websocket_server.py # WebSocket server for extension
└── protocol.py         # WebSocket message schemas
```

## Key Patterns

### Adding a New Command

1. Create handler in `commands/` (see existing files for patterns)
2. Add subparser in `cli.py`
3. Add dispatch in `cli.py` main function
4. Export from `commands/__init__.py`

### Email Processing Flow (Daemon)

```
ImapListener (IDLE)
    → on_new_email callback
    → loop.call_soon_threadsafe (thread-safe queue)
    → EmailProcessor.process_loop
    → _process_email (classify via LLM, update DB, optionally move)
```

### Bulk Classification Flow (classify command)

The classify command handles two types of emails:

1. **New emails** - Need LLM classification, processed concurrently
2. **Pre-classified but untransferred** - Transfer only with rate limiting

```
Source (Thunderbird/IMAP)
    → For each email:
        - If classified + transferred → skip
        - If classified + NOT transferred → add to transfer queue
        - If NOT classified → add to classify queue
    → Process classify queue (concurrent LLM calls)
    → Process transfer queue (sequential, rate-limited)
```

Use `--rate-limit SECS` to control delay between transfer operations (default: 1.0s).

### Sync and Transfer Commands

- `mailmap sync` - Sync DB transfer state with actual IMAP folder contents
  - Clears all transferred_at markers
  - Scans category folders on IMAP server
  - Marks found emails as transferred
  - Use `--dry-run` to preview changes

- `mailmap transfer` - Transfer pre-classified emails to IMAP (standalone)
  - Processes only classified but untransferred emails
  - Rate-limited to avoid overwhelming IMAP server
  - Use `--move` for move instead of copy

### Source/Target Abstraction

Sources yield `UnifiedEmail` objects from different backends:
- `ThunderbirdSource`: Local mbox files
- `ImapSource`: Direct IMAP fetch

Targets perform operations on classified emails:
- `ImapTarget`: Direct IMAP server operations
- `WebSocketTarget`: Via Thunderbird extension (manages its own server)

### Using Targets

Targets are self-contained and manage their own connections:

```python
from mailmap.targets import select_target

# select_target(config, target_account, websocket_port)
# - "imap": Direct IMAP connection
# - "local" + port: WebSocket to Thunderbird Local Folders
# - other + port: WebSocket to specific account

target = select_target(config, "imap")  # Direct IMAP
target = select_target(config, "local", websocket_port=9753)  # WebSocket

async with target:
    await target.create_folder("MyFolder")
    await target.copy_email(message_id, "MyFolder", raw_bytes)
    folders = await target.list_folders()
```

### Config Loading

```python
from mailmap.config import load_config
config = load_config(Path("config.toml"))
# Credentials from env: MAILMAP_IMAP_USERNAME, MAILMAP_IMAP_PASSWORD
```

### Database Access

```python
from mailmap.database import Database
db = Database("mailmap.db")
db.connect()
db.init_schema()
# ... operations ...
db.close()
```

### LLM Classification

```python
async with OllamaClient(config.ollama) as llm:
    result = await llm.classify_email(subject, from_addr, body, folder_descriptions)
    # result.predicted_folder, result.confidence
```

## Prompt Templates

Located in `mailmap/prompts/`. Use Python format strings:

- `classify_email.txt`: `{subject}`, `{from_addr}`, `{body}`, `{folders_text}`
- `generate_folder_description.txt`: `{folder_name}`, `{samples_text}`

## Database Schema

```sql
emails (
    message_id TEXT PRIMARY KEY,
    folder_id TEXT,
    subject TEXT,
    from_addr TEXT,
    mbox_path TEXT,
    classification TEXT,
    confidence REAL,
    is_spam INTEGER,
    spam_reason TEXT,
    processed_at TIMESTAMP,
    transferred_at TIMESTAMP  -- When email was copied/moved to target folder
)
```

## Environment Variables

| Variable | Usage |
|----------|-------|
| `MAILMAP_IMAP_USERNAME` | IMAP login |
| `MAILMAP_IMAP_PASSWORD` | IMAP password (never in config file) |
| `MAILMAP_WS_TOKEN` | WebSocket auth token |
