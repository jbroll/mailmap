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
# Edit config.toml with your IMAP credentials and Ollama settings
```

Requires Ollama running locally with a model (default: `qwen2.5:7b`).

## CLI Commands

```bash
# Run the daemon (monitors IMAP and classifies emails)
mailmap daemon

# Import from Thunderbird profile (generates descriptions + classifies)
mailmap import --server outlook.office365.com --folder INBOX --limit 1000

# Sync folders from IMAP and generate descriptions
mailmap sync

# Analyze emails and suggest folder structure
mailmap init --server outlook.office365.com --limit 500

# Learn categories from existing folder structure
mailmap learn --server outlook.office365.com

# Upload classified emails to IMAP folders
mailmap upload
mailmap upload --folder "Receipts"    # Only specific folder
mailmap upload --dry-run              # Preview without uploading

# List classification results
mailmap list
mailmap list --limit 100

# List folders and descriptions
mailmap folders

# Show classification summary with counts per category
mailmap summary

# Reset database (delete and start fresh)
mailmap reset
```

## Common Options

All subcommands support:
```bash
-c, --config PATH      # Config file (default: config.toml)
--db-path PATH         # Override database path
--ollama-url URL       # Override Ollama base URL
--ollama-model MODEL   # Override Ollama model name
```

Import/init/learn subcommands also support:
```bash
--profile PATH         # Thunderbird profile path
--server NAME          # Filter to specific IMAP server
--folder NAME          # Process only this folder (e.g., INBOX)
--limit N              # Max emails (integer) or percentage (0.1 = 10%)
--random               # Randomly sample instead of sequential
```

Example iteration workflow:
```bash
mailmap reset && mailmap import --limit 50 --ollama-model qwen2.5:3b
```

## Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_database.py

# Run specific test
pytest tests/test_database.py::TestFolderOperations::test_upsert_and_get_folder

# Run with verbose output
pytest -v
```

## Architecture

The system consists of these core modules in `mailmap/`:

- **config.py**: TOML-based configuration with dataclass models
- **content.py**: Email content cleaning (removes HTML, signatures, quotes, disclaimers)
- **database.py**: SQLite schema and operations for folders/emails/classifications
- **imap_client.py**: IMAP connection, IDLE monitoring, and polling
- **llm.py**: Ollama REST API client for classification and folder description generation
- **thunderbird.py**: Thunderbird profile reader for importing from mbox files in ImapMail cache
- **main.py**: CLI entry point and orchestration
- **prompts/**: Editable prompt templates for LLM interactions

## Database Schema

- `folders`: folder_id, name, description, last_updated
- `emails`: message_id, folder_id, subject, from_addr, mbox_path, classification, confidence, processed_at

## Prompt Templates

LLM prompts are stored in `mailmap/prompts/` as editable text files:

- **classify_email.txt**: Template for email classification
- **generate_folder_description.txt**: Template for generating folder descriptions from samples
- **suggest_folder_structure.txt**: Template for suggesting folder organization from email samples
- **refine_folder_structure.txt**: Template for iteratively refining folder categories
- **normalize_categories.txt**: Template for merging duplicate categories
- **repair_json.txt**: Template for fixing malformed JSON responses

Templates use Python format strings with placeholders like `{subject}`, `{body}`, `{folders_text}`, etc.
