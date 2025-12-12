# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mailmap is an email classification system that monitors IMAP servers and classifies emails into folders using a local GPU-accelerated LLM via MCP (Model Context Protocol).

## Build and Run

```bash
# Install dependencies
pip install -e .

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
```

Example iteration workflow:
```bash
mailmap --reset-db && mailmap --thunderbird --import-limit 50 --ollama-model qwen2.5:3b
```

## Architecture

The system consists of these core modules in `src/mailmap/`:

- **config.py**: TOML-based configuration with dataclass models
- **database.py**: SQLite schema and operations for folders/emails/classifications
- **imap_client.py**: IMAP connection, IDLE monitoring, and polling
- **llm.py**: Ollama REST API client for classification and folder description generation
- **mcp_server.py**: MCP server exposing `classify_email`, `update_folder_descriptions`, `get_folder_descriptions` tools
- **thunderbird.py**: Thunderbird profile reader for importing from mbox files in ImapMail cache
- **main.py**: CLI entry point and orchestration

## MCP Tools

- `classify_email` - Classify a single email given content and folder descriptions
- `update_folder_descriptions` - Generate/update folder summaries from sample emails
- `get_folder_descriptions` - Retrieve current folder descriptions

## Database Schema

- `folders`: folder_id, name, description, last_updated
- `emails`: message_id, folder_id, subject, from_addr, body_text, classification, confidence, processed_at
- `folder_email_map`: Tracks classification status relative to folder descriptions
