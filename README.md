# Mailmap

Email classification daemon that monitors an IMAP server and automatically organizes emails into folders using a local LLM and an embedding-based classifier.

## How It Works

Mailmap connects to an IMAP server and classifies incoming emails in two stages:

1. **Embedding fast-path** — each email is embedded via `nomic-embed-text` (768-dim float32). The embedding is compared to per-folder centroids (mean of all known embeddings for that folder). If the top match is clear enough, classification is instant.

2. **LLM fallback** — when the centroid match is ambiguous or a folder has too few examples, the email is sent to `qwen3:14b` via Ollama with a reasoning prompt that references your `categories.txt` descriptions.

As you accumulate examples and use `mailmap sync` to pick up manual corrections, the centroid classifier improves and the LLM is needed less often.

## Requirements

- Python 3.11+
- Ollama on a GPU host with `qwen3:14b` and `nomic-embed-text` pulled
- IMAP server with IDLE support

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `MAILMAP_IMAP_USERNAME` | IMAP login (required) |
| `MAILMAP_IMAP_PASSWORD` | IMAP password — never stored in config |
| `MAILMAP_WS_TOKEN` | WebSocket auth token (optional, Thunderbird extension only) |

### config.toml

```toml
[imap]
host = "imap.example.com"
port = 993
use_ssl = true
idle_folders = ["INBOX"]        # Folders monitored with IMAP IDLE
poll_interval_seconds = 300

[ollama]
base_url = "http://192.168.1.169:11434"   # GPU host
model = "qwen3:14b"
embed_model = "nomic-embed-text"
timeout_seconds = 300

[embedding]
enabled = true
min_similarity = 0.55   # Cosine similarity floor for fast-path
min_margin = 0.05       # top1 - top2 must exceed this
min_examples = 5        # Folders with fewer embeddings skip fast-path

[database]
path = "mailmap.db"
categories_file = "categories.txt"

[spam]
enabled = true
skip_folders = ["Junk", "Spam", "Trash", "Deleted Items", "Deleted"]
# rules: list of DSL rules — omit to use 40+ built-in defaults
```

### categories.txt

Defines destination folders and the descriptions the LLM uses to classify emails.

```
# CategoryName: Description (no spaces in name, can span lines)

Personal: A real person writing to you personally. The sender is an
individual using their own email address.

Financial: From a financial institution about your money. The sender's
primary business is managing financial assets — banks, brokerages, etc.

AccountSecurity: A security action is required or has occurred. Two-factor
codes, login alerts, password resets.
```

Write descriptions that define the **essence** of a category — what makes it uniquely itself — rather than listing specific senders or explicit exclusions.

```bash
mailmap categories         # List current categories
mailmap learn              # Generate categories from existing Thunderbird folders
mailmap init --limit 500   # Analyze emails and suggest folder structure
```

## First-Time Setup

### 1. Pull models on the GPU host

```bash
ollama pull qwen3:14b
ollama pull nomic-embed-text
```

### 2. Seed embeddings from existing folders

This scans every non-system IMAP folder, imports emails into the database with `classification = folder_name`, computes embeddings, and builds centroids — all before classifying anything new.

```bash
export MAILMAP_IMAP_USERNAME=you@example.com
export MAILMAP_IMAP_PASSWORD=yourpassword

mailmap embed --seed-from-imap
```

### 3. Classify INBOX

```bash
mailmap classify --source-type imap
```

### 4. Review results

```bash
mailmap summary
mailmap list --limit 100
```

### 5. Transfer (copy to category folders)

```bash
mailmap transfer --rate-limit 1.0
```

### 6. Start the daemon for ongoing classification

```bash
mailmap daemon --move
```

## CLI Commands

### Daemon

```bash
mailmap daemon                      # Monitor INBOX, classify new emails
mailmap daemon --move               # Also move classified emails to folders
mailmap daemon --process-existing   # Classify existing unclassified emails first
```

### Bulk Classification

```bash
mailmap classify                    # From Thunderbird cache (default)
mailmap classify --source-type imap # Directly from IMAP
mailmap classify --folder INBOX --limit 200
mailmap classify --force            # Re-classify already-processed emails
mailmap classify --concurrency 4    # Parallel LLM calls
mailmap classify --copy             # Copy to folders after classifying
mailmap classify --move             # Move to folders after classifying
mailmap classify --rate-limit 2.0   # Seconds between transfer ops
```

### Embeddings and Centroids

```bash
mailmap embed                       # Embed any classified emails missing embeddings
mailmap embed --seed-from-imap      # Import all IMAP folders into DB, then embed
mailmap embed --rebuild             # Wipe all embeddings/centroids and recompute
```

### Sync and Transfer

```bash
# Sync: marks transferred emails, detects manual moves, recomputes centroids
mailmap sync
mailmap sync --dry-run

# Transfer: move/copy pre-classified emails to IMAP folders
mailmap transfer
mailmap transfer --move
mailmap transfer --rate-limit 2.0
```

### Results and Maintenance

```bash
mailmap summary                     # Counts per category
mailmap list                        # Recent classifications
mailmap list --limit 200

mailmap clear                       # Clear all classifications (keeps emails)
mailmap clear --folder INBOX        # Clear only this folder's classifications
mailmap reset                       # Delete database entirely
mailmap dedup                       # Remove duplicate emails from category folders
mailmap dedup --dry-run
mailmap cleanup                     # Delete classification folders from IMAP
```

### Category Management

```bash
mailmap categories                  # List categories from categories.txt
mailmap learn                       # Generate descriptions from Thunderbird folders
mailmap init --limit 500            # Suggest folder structure from email samples
```

### IMAP Operations

```bash
mailmap folders                     # List all folders with counts
mailmap emails INBOX                # List emails in a folder
mailmap emails INBOX --limit 100
mailmap read INBOX 123              # Display email by UID

mailmap create-folder MyFolder
mailmap delete-folder MyFolder
mailmap move INBOX 123 Archive
mailmap copy INBOX 123 Archive
```

### Common Flags

All commands accept:

```bash
-c, --config PATH       Config file (default: config.toml)
-v, --verbose           Debug logging
--db-path PATH          Override database path
--ollama-url URL        Override Ollama base URL
--ollama-model MODEL    Override model name
```

Source commands (classify, learn, init) also accept:

```bash
--profile PATH          Thunderbird profile path
--folder SPEC           Process specific folder (e.g. INBOX or server.com:INBOX)
--source-type TYPE      'thunderbird' (default) or 'imap'
--limit N               Max emails: int for count, 0.1 for 10%
--random                Random sampling
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         CLI (cli.py)                             │
├──────────────────────────────────────────────────────────────────┤
│  daemon │ classify │ embed │ sync │ transfer │ imap_ops │ utils  │
├─────────────────────────────────────────────────────────────────-┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │              HybridClassifier (classifier.py)            │    │
│  │  Embedding fast-path  →  cosine(email, centroid)         │    │
│  │  LLM fallback         →  OllamaClient.classify_email()   │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐   │
│  │    Sources      │  │     Targets      │  │  Core         │   │
│  │  Thunderbird    │  │  ImapTarget      │  │  LLM client   │   │
│  │  ImapSource     │  │  WebSocketTarget │  │  EmbedClient  │   │
│  └─────────────────┘  └──────────────────┘  │  Database     │   │
│                                             │  Categories   │   │
│  ┌────────────────────────────────────────┐  │  Spam rules   │   │
│  │         ImapClient / ImapListener      │  │  Content      │   │
│  │  IDLE monitoring · batch header fetch  │  └───────────────┘   │
│  └────────────────────────────────────────┘                      │
└──────────────────────────────────────────────────────────────────┘
```

## Database Schema

```sql
emails (
    message_id    TEXT PRIMARY KEY,
    folder_id     TEXT NOT NULL,   -- original IMAP folder
    subject       TEXT,
    from_addr     TEXT,
    mbox_path     TEXT,            -- mbox path or empty for IMAP-sourced
    classification TEXT,           -- predicted destination folder
    confidence    REAL,
    is_spam       INTEGER DEFAULT 0,
    spam_reason   TEXT,            -- which rule matched
    processed_at  TIMESTAMP,
    transferred_at TIMESTAMP,      -- when copied/moved to target folder
    embedding     BLOB             -- float32 vector (nomic-embed-text, 768-dim)
);

folder_centroids (
    folder        TEXT PRIMARY KEY, -- classification name
    centroid      BLOB NOT NULL,    -- mean of all embeddings for this folder
    sample_count  INTEGER NOT NULL,
    updated_at    TIMESTAMP NOT NULL
);
```

Schema migrations run automatically on startup — existing databases gain new columns without data loss.

## Spam Detection

Mailmap includes 40+ header-based spam rules covering Microsoft/Office 365, SpamAssassin, Rspamd, Barracuda, Proofpoint, Cisco IronPort, and others. Spam emails are marked in the database and skipped for LLM classification. Configure in `[spam]`:

```toml
[spam]
enabled = true
skip_folders = ["Junk", "Spam", "Trash"]
rules = [
    "X-Spam-Score >= 5.0",
    "X-Spam-Flag == YES",
    "X-MS-Exchange-Organization-SCL >= 5",
]
```

Rule DSL: `HEADER [/REGEX/] OPERATOR VALUE`

Operators: `>=`, `>`, `<=`, `<`, `==`, `!=`, `prefix`, `suffix`, `contains`, `in`, `exists`

## Deployment

```bash
../deploy.sh/deploy.sh update .   # Deploy code changes to server
../deploy.sh/deploy.sh init .     # Full initial deployment with infrastructure
```

## Testing

```bash
pytest                          # All tests
pytest tests/test_embedding.py  # Embedding + classifier tests
pytest -v                       # Verbose
```
