# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mailmap is an email classification system that monitors IMAP servers and classifies emails into folders using a local GPU-accelerated LLM via MCP (Model Context Protocol).

## Build and Run

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (with dev tools for testing)
pip install -e ".[dev]"

# Copy and configure
cp config.example.toml config.toml

# Set credentials via environment variables (required)
export MAILMAP_IMAP_USERNAME="your-email@example.com"
export MAILMAP_IMAP_PASSWORD="your-password"
```

Requires Ollama running locally with a model (default: `qwen2.5:7b`).

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MAILMAP_IMAP_USERNAME` | IMAP username (for daemon, upload, --source-type imap) |
| `MAILMAP_IMAP_PASSWORD` | IMAP password (not allowed in config file) |
| `MAILMAP_WS_TOKEN` | WebSocket authentication token (optional) |

## CLI Commands

```bash
# Run the daemon (monitors IMAP and classifies emails)
mailmap daemon

# Daemon with automatic folder organization (moves emails after classification)
mailmap daemon --move

# Process existing unclassified INBOX emails on startup, then watch for new
mailmap daemon --move --process-existing

# Learn categories from existing Thunderbird folder structure
# Generates descriptions and saves to categories.txt
mailmap learn

# Bulk classify emails from Thunderbird using categories.txt
mailmap classify --limit 1000

# Classify specific folder (use server:folder if folder exists in multiple accounts)
mailmap classify --folder INBOX --limit 50
mailmap classify --folder outlook.office365.com:INBOX --limit 50

# Classify and copy to Thunderbird Local Folders (via extension)
mailmap classify --folder INBOX --limit 50 --copy --target-account local

# Classify and move to IMAP server folders (via extension)
mailmap classify --folder INBOX --limit 50 --move --target-account outlook.office365.com

# Force reading directly from IMAP instead of Thunderbird cache
mailmap classify --source-type imap --limit 100

# Analyze emails and suggest new folder structure (saves to categories.txt)
mailmap init --limit 500

# Upload classified emails to IMAP folders
mailmap upload
mailmap upload --folder "Receipts"    # Only specific folder
mailmap upload --dry-run              # Preview without uploading

# List classification results
mailmap list
mailmap list --limit 100

# List categories from categories.txt
mailmap categories

# Show classification summary with counts per category
mailmap summary

# Clear classifications (keeps emails, removes classifications)
mailmap clear
mailmap clear --folder INBOX          # Only specific source folder

# Reset database (delete and start fresh)
mailmap reset

# Delete classification folders from Thunderbird (via extension)
mailmap cleanup                                            # Delete from Local Folders
mailmap cleanup --target-account outlook.office365.com     # Delete from IMAP account

# IMAP folder and email management
mailmap folders                           # List folders with email counts
mailmap folders --source-type thunderbird # List from Thunderbird instead

mailmap emails INBOX                      # List emails in a folder
mailmap emails INBOX --limit 100          # Limit results

mailmap read INBOX 123                    # Read email by UID

mailmap create-folder MyFolder            # Create IMAP folder
mailmap delete-folder MyFolder            # Delete IMAP folder

mailmap move INBOX 123 Archive            # Move email to folder
mailmap copy INBOX 123 Archive            # Copy email to folder
```

## Typical Workflow

```bash
# 1. Learn categories from your existing Thunderbird folders
mailmap learn

# 2. Edit categories.txt as needed (human/LLM-friendly format)

# 3. Classify emails using those categories
mailmap classify --limit 500

# 4. Review results
mailmap summary
mailmap list --limit 20

# 5. Upload to IMAP folders
mailmap upload --dry-run
mailmap upload
```

## Common Options

All subcommands support:
```bash
-c, --config PATH      # Config file (default: config.toml)
--db-path PATH         # Override database path
--ollama-url URL       # Override Ollama base URL
--ollama-model MODEL   # Override Ollama model name
```

Thunderbird subcommands (learn/classify/init) also support:
```bash
--profile PATH         # Thunderbird profile path
--folder SPEC          # Process only this folder (e.g., INBOX or server:INBOX)
--limit N              # Max emails (integer) or percentage (0.1 = 10%)
--random               # Randomly sample instead of sequential
--source-type TYPE     # Email source: 'thunderbird' (local cache) or 'imap' (direct)
```

Note: If a folder name exists in multiple accounts, use `server:folder` syntax
(e.g., `outlook.office365.com:INBOX`) to disambiguate.

Classify command also supports (requires Thunderbird extension):
```bash
--copy                 # Copy classified emails to target folders
--move                 # Move classified emails to target folders
--target-account DEST  # Target: 'local' (default) or server name (e.g., outlook.office365.com)
```

Daemon command options:
```bash
--move                 # Move classified emails to IMAP folders (creates folders if needed)
--process-existing     # Process existing unclassified emails in INBOX on startup
```

## Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_database.py

# Run with verbose output
pytest -v
```

## Architecture

The system consists of these core modules in `mailmap/`:

### Core Modules
- **config.py**: TOML-based configuration with dataclass models
- **categories.py**: Load/save categories from editable text file (categories.txt)
- **content.py**: Email content cleaning (removes HTML, signatures, quotes, disclaimers)
- **database.py**: SQLite schema and operations for emails/classifications
- **email.py**: UnifiedEmail dataclass for source-agnostic email representation
- **imap_client.py**: IMAP connection, IDLE monitoring, and polling
- **llm.py**: Ollama REST API client for classification and folder description generation
- **spam.py**: Spam detection from email headers using configurable rules
- **thunderbird.py**: Thunderbird profile reader for importing from mbox files
- **websocket_server.py**: WebSocket server for Thunderbird extension communication
- **protocol.py**: WebSocket message schemas (Request, Response, Event)

### CLI and Commands
- **main.py**: Entry point (imports from cli.py)
- **cli.py**: Argument parsing and command dispatch
- **commands/**: Command implementations
  - **daemon.py**: IMAP listener and EmailProcessor for real-time classification
  - **classify.py**: Bulk email classification with optional copy/move
  - **learn.py**: Learn categories from existing Thunderbird folder structure
  - **init.py**: Analyze emails and suggest folder structure
  - **upload.py**: Upload classified emails to IMAP, cleanup Thunderbird folders
  - **imap_ops.py**: IMAP folder/email management (list, read, create, delete, move, copy)
  - **utils.py**: Utility commands (list, summary, clear, reset, categories)

### Abstractions
- **sources/**: Email source abstractions (ThunderbirdSource, ImapSource)
- **targets/**: Email target abstractions (WebSocketTarget, ImapTarget)
- **prompts/**: Editable prompt templates for LLM interactions

## Categories File

Categories are stored in `categories.txt` (human/LLM-editable format):

```
Financial: Banking, investments, and brokerage communications. Includes
account statements, trade confirmations, and tax documents.

Receipts: Purchase receipts, invoices, and payment confirmations.

Orders: Order confirmations and shipping notifications.
```

## Database Schema

- `emails`: message_id, folder_id, subject, from_addr, mbox_path, classification, confidence, is_spam, spam_reason, processed_at

## Prompt Templates

LLM prompts are stored in `mailmap/prompts/` as editable text files:

- **classify_email.txt**: Template for email classification
- **generate_folder_description.txt**: Template for generating folder descriptions from samples
- **suggest_folder_structure.txt**: Template for suggesting folder organization from email samples
- **refine_folder_structure.txt**: Template for iteratively refining folder categories
- **normalize_categories.txt**: Template for merging duplicate categories
- **repair_json.txt**: Template for fixing malformed JSON responses

Templates use Python format strings with placeholders like `{subject}`, `{body}`, `{folders_text}`, etc.
