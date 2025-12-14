# Mailmap

Email classification daemon that monitors IMAP servers and automatically organizes emails into folders using a local LLM.

## Overview

Mailmap connects to an IMAP server, monitors specified folders (typically INBOX) using IMAP IDLE, and classifies incoming emails using an Ollama-hosted LLM. Classified emails can optionally be moved to destination folders on the IMAP server.

The system also supports bulk classification of existing emails from Thunderbird's local cache or directly from IMAP.

## Requirements

- Python 3.12+
- Ollama with a language model (default: `qwen2.5:7b`)
- IMAP server with IDLE support

## Installation

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install with development dependencies
pip install -e ".[dev]"

# Copy and configure
cp config.example.toml config.toml
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `MAILMAP_IMAP_USERNAME` | IMAP username (required for daemon) |
| `MAILMAP_IMAP_PASSWORD` | IMAP password (required, not stored in config) |
| `MAILMAP_WS_TOKEN` | WebSocket authentication token (optional) |

### Config File (config.toml)

```toml
[imap]
host = "imap.example.com"
port = 993
use_ssl = true
idle_folders = ["INBOX"]
poll_interval_seconds = 300

[ollama]
base_url = "http://localhost:11434"
model = "qwen2.5:7b"
timeout_seconds = 120

[database]
path = "mailmap.db"
categories_file = "categories.txt"

[spam]
enabled = true
skip_folders = ["Junk", "Spam", "Trash"]
```

### Categories File (categories.txt)

Categories define the destination folders and their descriptions for classification. The LLM uses these descriptions to decide where each email belongs.

**Format:**
```
CategoryName: Description text that can span
multiple lines until a blank line.

AnotherCategory: Another description.

# Comments start with #
```

**Rules:**
- Category names: no spaces, alphanumeric + underscore
- Descriptions can span multiple lines (until blank line)
- Lines starting with `#` are comments
- Blank lines separate categories

**Writing Effective Descriptions:**

Use a **discriminative approach** - focus on what makes each category unique rather than listing everything it contains. Describe:
- WHO sends these emails (type of sender)
- WHAT the primary intent is
- What explicitly does NOT belong (to avoid confusion)

**Example:**
```
Financial: Communications FROM financial institutions (banks, brokerages,
credit unions) ABOUT your accounts, statements, or investments. The sender
must be a company whose primary business is managing money. NOT payment
receipts from regular companies - those go to Receipts.

Receipts: Payment confirmations and invoices FROM any company (except
financial institutions) AFTER a transaction. The primary intent is "we
received your payment" or "here's your bill." NOT order status updates.

Orders: Order confirmations and shipping notifications. Status updates
about items you've purchased - placed, shipped, delivered, delayed.

AccountSecurity: Security-critical messages requiring immediate action.
Password resets, 2FA codes, login alerts, suspicious activity warnings.

Personal: Personal correspondence from friends and family.
```

**Commands:**
```bash
mailmap categories          # List current categories
mailmap learn               # Generate categories from existing Thunderbird folders
mailmap init --limit 500    # Analyze emails and suggest folder structure
```

## CLI Commands

### Daemon Mode

```bash
# Monitor IMAP and classify new emails
mailmap daemon

# Classify and move emails to destination folders
mailmap daemon --move

# Process existing unclassified emails on startup
mailmap daemon --move --process-existing
```

### Bulk Classification

```bash
# Classify emails from Thunderbird cache
mailmap classify --limit 1000

# Classify specific folder
mailmap classify --folder INBOX --limit 50

# Classify from IMAP directly
mailmap classify --source-type imap --limit 100

# Classify and move via Thunderbird extension
mailmap classify --folder INBOX --move --target-account outlook.office365.com
```

### Category Management

```bash
# Learn categories from existing Thunderbird folders
mailmap learn

# Analyze emails and suggest folder structure
mailmap init --limit 500

# List categories from categories.txt
mailmap categories
```

### Results and Maintenance

```bash
# List classification results
mailmap list
mailmap list --limit 100

# Show summary with counts per category
mailmap summary

# Upload classified emails to IMAP folders
mailmap upload
mailmap upload --dry-run

# Clear classifications (keeps emails)
mailmap clear

# Reset database
mailmap reset
```

### IMAP Operations

```bash
# List folders with email counts
mailmap folders

# List emails in a folder
mailmap emails INBOX --limit 100

# Read email by UID
mailmap read INBOX 123

# Folder management (default: direct IMAP)
mailmap create-folder MyFolder
mailmap delete-folder MyFolder

# Folder management via Thunderbird extension
mailmap create-folder MyFolder --target-account local --websocket
mailmap delete-folder MyFolder --target-account imap --websocket

# Move/copy emails
mailmap move INBOX 123 Archive
mailmap copy INBOX 123 Archive

# Cleanup classification folders from target
mailmap cleanup --target-account imap
mailmap cleanup --target-account local --websocket
```

## Common Options

All commands support:

```bash
-c, --config PATH      # Config file (default: config.toml)
--db-path PATH         # Override database path
--ollama-url URL       # Override Ollama base URL
--ollama-model MODEL   # Override Ollama model name
```

Thunderbird commands (learn/classify/init) also support:

```bash
--profile PATH         # Thunderbird profile path
--folder SPEC          # Process specific folder (e.g., INBOX or server:INBOX)
--limit N              # Max emails (integer or fraction like 0.1 for 10%)
--random               # Random sampling instead of sequential
--source-type TYPE     # 'thunderbird' (default) or 'imap'
```

Target commands (classify, cleanup, create-folder, delete-folder) support:

```bash
--target-account ACCT  # 'local' (Thunderbird), 'imap' (direct), or account ID
--websocket [PORT]     # Use WebSocket (default port: 9753)
```

## Typical Workflow

```bash
# 1. Learn categories from existing Thunderbird folders
mailmap learn

# 2. Edit categories.txt as needed

# 3. Bulk classify existing emails
mailmap classify --limit 500

# 4. Review results
mailmap summary

# 5. Upload to IMAP or run daemon for ongoing classification
mailmap upload
# or
mailmap daemon --move
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI (cli.py)                           │
├─────────────────────────────────────────────────────────────────┤
│  daemon  │  classify  │  learn  │  upload  │  imap_ops  │ utils │
├──────────┴────────────┴─────────┴──────────┴────────────┴───────┤
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Sources   │  │   Targets   │  │      Core Services      │  │
│  │             │  │             │  │                         │  │
│  │ Thunderbird │  │  WebSocket  │  │  LLM (Ollama client)    │  │
│  │    IMAP     │  │    IMAP     │  │  Database (SQLite)      │  │
│  └─────────────┘  └─────────────┘  │  Categories (txt file)  │  │
│                                    │  Content (email parser) │  │
│                                    │  Spam (header rules)    │  │
│                                    └─────────────────────────┘  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    IMAP Client                          │    │
│  │  ImapMailbox (sync ops)  │  ImapListener (IDLE/poll)   │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Components

**CLI Layer** (`cli.py`, `commands/`)
- Argument parsing and command dispatch
- Commands: daemon, classify, learn, init, upload, imap_ops, utils

**Email Sources** (`sources/`)
- `ThunderbirdSource`: Reads from local Thunderbird mbox cache
- `ImapSource`: Reads directly from IMAP server

**Email Targets** (`targets/`)
- `ImapTarget`: Direct IMAP server operations
- `WebSocketTarget`: Via Thunderbird extension (self-contained, manages its own server)
- Both implement `EmailTarget` protocol with `create_folder`, `delete_folder`, `list_folders`, `copy_email`, `move_email`

**Core Services**
- `llm.py`: Ollama REST API client for classification
- `database.py`: SQLite storage for emails and classifications
- `categories.py`: Load/save category definitions
- `content.py`: Email body extraction and cleaning
- `spam.py`: Header-based spam detection

**IMAP Client** (`imap_client.py`)
- `ImapMailbox`: Connection management, folder operations, email fetch/move
- `ImapListener`: Async IDLE monitoring with fallback polling

**Prompt Templates** (`prompts/`)
- Editable text files for LLM prompts
- `classify_email.txt`, `generate_folder_description.txt`, etc.

## Database Schema

Single table storing emails and their classifications:

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
    processed_at TIMESTAMP
)
```

## Testing

```bash
pytest              # Run all tests
pytest -v           # Verbose output
pytest tests/test_database.py  # Specific file
```

## Deployment

For systemd deployment, see `deploy.conf` which configures the binary_service module from deploy.sh.

```bash
# Deploy to remote host
../deploy.sh/deploy.sh init .   # First time
../deploy.sh/deploy.sh update . # Updates
```
