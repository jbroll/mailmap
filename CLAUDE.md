# CLAUDE.md

Developer guidance for Claude Code when working with this repository.

## Quick Start

```bash
uv sync                         # .venv from uv.lock, dev group included

export MAILMAP_IMAP_USERNAME="your-email@example.com"
export MAILMAP_IMAP_PASSWORD="your-password"
```

## Testing

```bash
uv run pytest                          # All tests
uv run pytest tests/test_database.py   # Specific file
uv run pytest -v                       # Verbose
uv run ruff check .                    # Lint
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
│   ├── embed.py        # Embedding bootstrap and centroid rebuild
│   ├── learn.py        # Learn categories from Thunderbird
│   ├── init.py         # Suggest folder structure
│   ├── upload.py       # Upload to IMAP, cleanup
│   ├── imap_ops.py     # IMAP management commands
│   └── utils.py        # list, summary, clear, reset, sync (+ drift detection)
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
├── imap_client.py      # ImapClient (+ fetch_all_headers), ImapListener
├── llm.py              # Ollama REST client (OllamaClient)
├── embedding.py        # EmbeddingClient + pure-stdlib vector math
├── classifier.py       # HybridClassifier (centroid fast-path + LLM fallback)
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

### Classification Pipeline (Hybrid)

```
New email
    │
    ▼
EmbeddingClient.embed(text)   →  768-dim float32 vector
    │
    ▼
HybridClassifier.classify(email_dict, folder_descriptions)
    ├─ cosine(vec, centroid) for each folder
    ├─ top_sim >= min_similarity AND margin >= min_margin?
    │     ├─ YES → fast path (return centroid match)
    │     └─ NO  → LLM fallback (OllamaClient.classify_email)
    ▼
ClassificationResult(predicted_folder, confidence, ...)
DB update: classification + embedding stored
```

Thresholds (config `[embedding]`): `min_similarity=0.55`, `min_margin=0.05`,
`min_examples=5` (folders with fewer embeddings are skipped in centroid pass).

### Email Processing Flow (Daemon)

```
ImapListener (IDLE)
    → on_new_email callback
    → loop.call_soon_threadsafe (thread-safe queue)
    → EmailProcessor.process_loop
    → _process_email
        → HybridClassifier.classify (embed fast-path or LLM fallback)
        → DB update
        → optionally move email to folder
```

### Bulk Classification Flow (classify command)

The classify command handles two types of emails:

1. **New emails** — classified by HybridClassifier, processed concurrently
2. **Pre-classified but untransferred** — transfer only with rate limiting

```
Source (Thunderbird/IMAP)
    → For each email:
        - If classified + transferred → skip
        - If classified + NOT transferred → add to transfer queue
        - If NOT classified → add to classify queue
    → Process classify queue (concurrent HybridClassifier calls)
    → Process transfer queue (sequential, rate-limited)
```

Use `--rate-limit SECS` to control delay between transfer operations (default: 1.0s).

### Embedding Bootstrap (embed command)

Run once before classifying to seed embeddings from existing IMAP folder structure:

```bash
mailmap embed --seed-from-imap   # Import all folders → embed → build centroids
mailmap embed                    # Embed any classified emails missing embeddings
mailmap embed --rebuild          # Wipe all embeddings/centroids and recompute
```

`--seed-from-imap` scans every non-system IMAP folder (skips INBOX, Drafts,
Sent, Trash, Junk, Archive), imports emails with `classification = folder_name`,
then embeds everything and builds centroids. Folders not in `categories.txt`
are still imported — their centroids become training signal.

### Sync: Transfer State + Drift Detection

`mailmap sync` does two things:

1. **Transfer state**: Scans IMAP category folders, marks emails as transferred
   in the DB. Use `--dry-run` to preview.

2. **Drift detection**: For every email found on the server, if its
   `classification` in the DB differs from the folder it's actually in, the DB
   is updated to match and affected centroids are rebuilt. User manual moves
   in Thunderbird automatically become training data.

```
sync
    → clear transferred_at markers
    → scan each category folder on IMAP
    → for each message_id found:
        - mark as transferred
        - if DB classification != current folder → update classification,
          add both folders to dirty_folders
    → recompute centroids for dirty_folders
```

### Source/Target Abstraction

Sources yield `UnifiedEmail` objects from different backends:
- `ThunderbirdSource`: Local mbox files
- `ImapSource`: Direct IMAP fetch

Targets perform operations on classified emails:
- `ImapTarget`: Direct IMAP server operations
- `WebSocketTarget`: Via Thunderbird extension (manages its own server)

### Using Targets

```python
from mailmap.targets import select_target

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

### LLM Classification (direct)

```python
async with OllamaClient(config.ollama) as llm:
    result = await llm.classify_email(subject, from_addr, body, folder_descriptions)
    # result.predicted_folder, result.confidence
```

### Embedding (direct)

```python
from mailmap.embedding import EmbeddingClient, cosine, centroid, vec_from_bytes

async with EmbeddingClient(config.ollama) as embedder:
    blob = await embedder.embed("Subject: ...\nFrom: ...")
    blobs = await embedder.embed_batch(["text1", "text2"])
```

Vector math uses stdlib `array.array('f')` — no numpy dependency.

### Hybrid Classifier (direct)

```python
from mailmap.classifier import HybridClassifier

async with OllamaClient(config.ollama) as llm, \
           EmbeddingClient(config.ollama) as embedder:
    classifier = HybridClassifier(config, db, llm, embedder)
    result = await classifier.classify(
        {"subject": ..., "from": ..., "body": ...,
         "message_id": ..., "attachments": []},
        folder_descriptions={"FolderName": "Description...", ...},
    )
    # result.predicted_folder, result.confidence
```

Centroids are loaded lazily from DB and cached per-process. Call
`classifier.invalidate_centroid(folder)` after manual updates.

## Prompt Templates

Located in `mailmap/prompts/`. Use Python format strings:

- `classify_email.txt`: `{subject}`, `{from_addr}`, `{body}`, `{folders_text}`, `{attachments_section}`
- `generate_folder_description.txt`: `{folder_name}`, `{samples_text}`

The classify prompt uses a calibrated confidence scale with inverted framing
("how often would this be wrong") and an anchored scale (0.95/0.80/0.65/0.50).

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
    spam_reason   TEXT,
    processed_at  TIMESTAMP,
    transferred_at TIMESTAMP,      -- when copied/moved to target folder
    embedding     BLOB             -- float32 vector (nomic-embed-text, 768-dim)
);

folder_centroids (
    folder        TEXT PRIMARY KEY, -- classification name
    centroid      BLOB NOT NULL,    -- mean of all embeddings (3072 bytes)
    sample_count  INTEGER NOT NULL,
    updated_at    TIMESTAMP NOT NULL
);
```

Schema migrations run automatically on startup — existing databases gain new
columns without data loss.

## Environment Variables

| Variable | Usage |
|----------|-------|
| `MAILMAP_IMAP_USERNAME` | IMAP login |
| `MAILMAP_IMAP_PASSWORD` | IMAP password (never in config file) |
| `MAILMAP_WS_TOKEN` | WebSocket auth token |
