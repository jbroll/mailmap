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

# Run the daemon (monitors IMAP and classifies emails)
mailmap -c config.toml

# Sync folders and generate descriptions only
mailmap -c config.toml --sync-folders

# Run as MCP server only (for external MCP clients)
mailmap -c config.toml --mcp

# Import from Thunderbird profile (generates descriptions + classifies)
mailmap -c config.toml --thunderbird

# Upload classified emails to IMAP folders
mailmap -c config.toml --upload

# Upload only emails classified to a specific folder (for incremental testing)
mailmap -c config.toml --upload --upload-folder "Receipts"

# Preview what would be uploaded (dry run)
mailmap -c config.toml --upload-dry-run
mailmap -c config.toml --upload-dry-run --upload-folder "Receipts"

# Reset database (delete and start fresh)
mailmap -c config.toml --reset-db
```

Requires Ollama running locally with a model (default: `qwen2.5:7b`).

## CLI Config Overrides

Override config file values from command line:

```bash
--db-path PATH              # Database file path
--ollama-url URL            # Ollama base URL
--ollama-model MODEL        # Ollama model name
--thunderbird-profile PATH  # Thunderbird profile path
--thunderbird-server NAME   # Filter to specific IMAP server
--import-limit N            # Max emails per folder
--samples-per-folder N      # Emails to sample for descriptions
--init-sample-limit N       # Max emails for --init-folders mode
```

Example iteration workflow:
```bash
mailmap --reset-db && mailmap --thunderbird --import-limit 50 --ollama-model qwen2.5:3b
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
