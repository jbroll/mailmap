# Modernization Code Examples

Quick reference for implementing each recommendation with detailed before/after code.

---

## 1. SQLite Datetime Adapter Fix

### Before
```python
# mailmap/database.py (current)
from datetime import datetime

@dataclass
class Email:
    processed_at: datetime | None = None

def insert_email(self, email: Email) -> None:
    self.conn.execute(
        "INSERT ... processed_at VALUES ...",
        (
            # ... other fields ...
            email.processed_at,  # DeprecationWarning in 3.12, breaks in 3.13
        ),
    )
```

### After
```python
# mailmap/database.py (modernized)
from datetime import datetime

def serialize_datetime(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO 8601 for SQLite."""
    if dt is None:
        return None
    return dt.isoformat()

def deserialize_datetime(iso_str: str | None) -> datetime | None:
    """Deserialize ISO 8601 string to datetime."""
    if iso_str is None:
        return None
    return datetime.fromisoformat(iso_str)

@dataclass
class Email:
    # Store as ISO 8601 string in DB, convert on retrieval
    processed_at: str | None = None

def insert_email(self, email: Email) -> None:
    # Process datetime at call site
    processed_at = email.processed_at
    if isinstance(processed_at, str):
        processed_at = serialize_datetime(deserialize_datetime(processed_at))

    self.conn.execute(
        "INSERT ... processed_at VALUES ...",
        (
            # ... other fields ...
            processed_at,  # String - no deprecation warning
        ),
    )

def _row_to_email(self, row: sqlite3.Row) -> Email:
    """Convert database row to Email."""
    return Email(
        # ... other fields ...
        processed_at=row["processed_at"],  # String from DB
    )

def update_classification(
    self, message_id: str, classification: str, confidence: float
) -> None:
    """Update classification with modern datetime handling."""
    self.conn.execute(
        "UPDATE emails SET classification=?, confidence=?, processed_at=? WHERE message_id=?",
        (
            classification,
            confidence,
            serialize_datetime(datetime.now()),  # Convert before insert
            message_id
        ),
    )
    self.conn.commit()
```

**Migration helper for existing databases:**
```python
def _migrate_datetime_strings() -> None:
    """One-time migration: convert datetime objects to ISO 8601 strings.

    Run this once after code update to fix any existing raw datetime objects.
    """
    cursor = self.conn.execute(
        "SELECT message_id, processed_at, transferred_at FROM emails"
    )
    rows = cursor.fetchall()

    for row in rows:
        msg_id = row["message_id"]
        # If these are already strings, fromisoformat works
        # If they're Python datetime objects, this won't work (db isn't corrupt)
        # Just skip - the new code handles strings
        try:
            processed = serialize_datetime(
                deserialize_datetime(row["processed_at"])
            )
            transferred = serialize_datetime(
                deserialize_datetime(row["transferred_at"])
            )

            self.conn.execute(
                "UPDATE emails SET processed_at=?, transferred_at=? WHERE message_id=?",
                (processed, transferred, msg_id)
            )
        except (ValueError, TypeError):
            # Row already in new format, skip
            pass

    self.conn.commit()
```

---

## 2. TypedDict for Type Safety

### Before
```python
# mailmap/llm.py
from typing import Any

async def classify_email(
    self,
    subject: str,
    from_addr: str,
    body: str,
    folder_descriptions: dict[str, str],  # Unstructured
    attachments: list[dict] | None = None,  # What keys exist?
) -> ClassificationResult:
    # ...
    cleaned = extract_email_summary(
        subject, from_addr, body, max_body_length=500, attachments=attachments
    )
    # No type hints for what extract_email_summary returns
    attachments_section = ""
    if cleaned.get("attachments"):  # Accessing dict keys without type hint
        attachments_section = f"Attachments:\n{cleaned['attachments']}\n"
```

### After
```python
# mailmap/types.py (NEW FILE)
from typing import NotRequired, TypedDict

class AttachmentInfo(TypedDict):
    """Attachment metadata from email parsing.

    Note: text_content is only present for text-based attachments.
    """
    filename: str
    content_type: str
    text_content: NotRequired[str | None]

class EmailSummary(TypedDict):
    """Result of extract_email_summary() for LLM processing."""
    subject: str
    from_addr: str
    body: str
    attachments: NotRequired[str]  # Formatted attachment text

# mailmap/llm.py
from mailmap.types import AttachmentInfo, EmailSummary

async def classify_email(
    self,
    subject: str,
    from_addr: str,
    body: str,
    folder_descriptions: dict[str, str],
    attachments: list[AttachmentInfo] | None = None,  # Now typed!
) -> ClassificationResult:
    # ...
    cleaned: EmailSummary = extract_email_summary(
        subject, from_addr, body, max_body_length=500, attachments=attachments
    )
    # IDE knows cleaned has subject, from_addr, body, attachments keys
    attachments_section = ""
    if cleaned.get("attachments"):
        attachments_section = f"Attachments:\n{cleaned['attachments']}\n"

# mailmap/imap_client.py
from mailmap.types import AttachmentInfo

def extract_attachments(msg: email.message.Message) -> list[AttachmentInfo]:
    """Extract attachment metadata."""
    attachments: list[AttachmentInfo] = []
    for part in msg.walk():
        # ... extract logic ...
        attachment_info: AttachmentInfo = {
            "filename": filename,
            "content_type": content_type,
            "text_content": text,  # May be None, that's OK
        }
        attachments.append(attachment_info)
    return attachments
```

**Benefits:**
- mypy catches missing keys: `cleaned['missing_key']` → error
- IDE autocomplete shows all possible keys
- Documentation clear about optional fields

---

## 3. Protocol for Polymorphism

### Before
```python
# sources/imap.py
class ImapSource:
    def get_emails(self) -> Iterator[UnifiedEmail]:
        """Fetch emails from IMAP server."""
        # Implementation

    def close(self) -> None:
        """Close IMAP connection."""

# sources/thunderbird.py
class ThunderbirdSource:
    def get_emails(self) -> Iterator[UnifiedEmail]:
        """Yield emails from Thunderbird cache."""
        # Different implementation

    def close(self) -> None:
        """Close (no-op for local files)."""

# Usage - no type safety that both implement same interface
def process_source(source):  # source: what type?
    """Process emails from any source."""
    for email in source.get_emails():  # Assumes get_emails exists
        handle_email(email)
```

### After
```python
# mailmap/protocols.py (NEW FILE)
from typing import Iterator, Protocol, runtime_checkable
from mailmap.email import UnifiedEmail

@runtime_checkable
class EmailSource(Protocol):
    """Protocol: anything that yields UnifiedEmail objects."""

    def get_emails(self) -> Iterator[UnifiedEmail]:
        """Yield email messages."""
        ...

    def close(self) -> None:
        """Clean up resources (optional)."""
        ...

@runtime_checkable
class EmailTarget(Protocol):
    """Protocol: anything that stores UnifiedEmail objects."""

    def send_email(self, email: UnifiedEmail, folder: str) -> bool:
        """Store email in folder. Return True if successful."""
        ...

    def create_folder(self, folder: str) -> bool:
        """Create folder if missing. Return True if created."""
        ...

    def close(self) -> None:
        """Clean up resources (optional)."""
        ...

# sources/imap.py
from mailmap.protocols import EmailSource

class ImapSource:
    """Implements EmailSource protocol."""

    def get_emails(self) -> Iterator[UnifiedEmail]:
        # ...

    def close(self) -> None:
        # ...

# Usage - NOW type safe!
from mailmap.protocols import EmailSource

def process_source(source: EmailSource) -> None:
    """Type-checked to accept any EmailSource implementation."""
    for email in source.get_emails():
        handle_email(email)
    source.close()

# Type checker verifies source has get_emails and close methods
process_source(ImapSource(config))  # ✓ OK
process_source(ThunderbirdSource(config))  # ✓ OK
process_source("not a source")  # ✗ Type error caught early
```

---

## 4. Context Managers for Resource Safety

### Before
```python
# mailmap/imap_client.py (current)
def watch_folder_idle(self, folder: str, callback: Callable) -> None:
    mailbox = ImapMailbox(self.config)
    try:
        mailbox.connect()
        logger.info(f"Connected to {folder}")
        uids = mailbox.fetch_recent_uids(folder)
        # ... IDLE loop ...
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        mailbox.disconnect()  # Manual cleanup required

# Risk: If exception happens before disconnect, connection leaks
# (OK with finally, but verbose and easy to miss)
```

### After
```python
# mailmap/imap_client.py (modernized)
class ImapMailbox:
    def __enter__(self) -> "ImapMailbox":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - always called."""
        self.disconnect()
        # Return None to propagate exceptions
        return None

# Usage - automatic cleanup
async def watch_folder_idle(self, folder: str, callback: Callable) -> None:
    with ImapMailbox(self.config) as mailbox:
        logger.info(f"Connected to {folder}")
        uids = mailbox.fetch_recent_uids(folder)
        # ... IDLE loop ...
        # disconnect() called automatically, even on exception

# Even cleaner with contextlib in daemon.py
from contextlib import ExitStack

with ExitStack() as stack:
    mailbox = stack.enter_context(ImapMailbox(config))
    http_client = stack.enter_context(OllamaClient(llm_config))
    # Both cleaned up automatically when exiting block
```

---

## 5. Frozen Dataclasses

### Before
```python
# mailmap/llm.py
@dataclass
class ClassificationResult:
    predicted_folder: str
    secondary_labels: list[str]
    confidence: float

# Accidental mutation - nothing prevents this
result = ClassificationResult("INBOX", [], 0.95)
result.confidence = -999  # OOPS - invalid state!
result.secondary_labels.append("spam")  # Unexpected side effect
```

### After
```python
# mailmap/llm.py
@dataclass(frozen=True)
class ClassificationResult:
    """Immutable classification result."""
    predicted_folder: str
    secondary_labels: list[str]
    confidence: float

result = ClassificationResult("INBOX", [], 0.95)
result.confidence = -999  # FrozenInstanceError - caught at runtime
result.secondary_labels.append("spam")  # TypeError - list itself is mutable

# To make fully immutable, use tuple:
@dataclass(frozen=True)
class ClassificationResult:
    predicted_folder: str
    secondary_labels: tuple[str, ...]  # Tuple, not list
    confidence: float

result = ClassificationResult("INBOX", (), 0.95)
result.secondary_labels = ()  # FrozenInstanceError
result.secondary_labels[0] = "x"  # TypeError (tuple immutable)
```

**For frequently created objects, use slots:**
```python
# mailmap/imap_client.py
@dataclass(slots=True)  # Memory efficient
class EmailMessage:
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str
    uid: int
    attachments: list[dict] | None = None

# Before: EmailMessage.__dict__ exists, takes ~96 bytes extra per instance
# After: No __dict__, saves memory for thousands of emails
```

---

## 6. Match Statements

### Before
```python
# mailmap/spam.py (current)
if rule.operator == Operator.GTE:
    return num_value >= rule.value
elif rule.operator == Operator.GT:
    return num_value > rule.value
elif rule.operator == Operator.LTE:
    return num_value <= rule.value
elif rule.operator == Operator.LT:
    return num_value < rule.value
else:
    return False
```

### After
```python
# mailmap/spam.py (Python 3.10+ match statement)
match rule.operator:
    case Operator.GTE:
        return num_value >= rule.value
    case Operator.GT:
        return num_value > rule.value
    case Operator.LTE:
        return num_value <= rule.value
    case Operator.LT:
        return num_value < rule.value
    case _ as invalid:
        logger.warning(f"Unexpected operator: {invalid}")
        return False
```

**More complex example in imap_client.py:**
```python
# Before: nested if/elif for attachment parsing
if content_type.startswith("text/"):
    if content_type in ("text/plain", "text/html"):
        pass  # Skip inline body
    elif content_type in ("text/calendar", "text/csv", "text/xml"):
        parse_text(part)
elif content_type in ("application/json", "application/pdf"):
    parse_binary(part)

# After: Match statement
match content_type:
    case "text/plain" | "text/html":
        pass  # Skip inline body
    case "text/calendar" | "text/csv" | "text/xml":
        parse_text(part)
    case "application/json" | "application/pdf":
        parse_binary(part)
    case other if other.startswith("text/"):
        logger.debug(f"Unhandled text type: {other}")
    case _:
        logger.debug(f"Attachment type: {content_type}")
```

---

## 7. Union Type Cleanup

### Before
```python
from typing import Optional, Union, List

def foo(name: Optional[str]) -> Union[int, None]:
    items: List[str] = []
    return None
```

### After
```python
# No imports needed for basic types!
def foo(name: str | None) -> int | None:
    items: list[str] = []
    return None
```

**One-liner to fix all at once:**
```bash
ruff check --fix --select UP mailmap/ tests/
```

This uses ruff's `UP` (pyupgrade) rules to:
- Convert `Optional[X]` → `X | None`
- Convert `List[X]` → `list[X]`
- Remove unnecessary imports
- Update `Union` usage

---

## 8. Better Mypy Configuration

### Before
```toml
# pyproject.toml
[tool.mypy]
python_version = "3.11"
warn_unused_ignores = true
ignore_missing_imports = true  # Too permissive!
```

```bash
# Only checks obvious errors
mypy mailmap/
# ... many things pass silently ...
```

### After
```toml
# pyproject.toml
[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
warn_unused_configs = true
check_untyped_defs = true
disallow_incomplete_defs = true
no_implicit_optional = true
warn_no_return = true

# Only ignore packages that genuinely have no stubs
[[tool.mypy.overrides]]
module = "imapclient.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "html2text"
ignore_missing_imports = true
```

```bash
# Now catches more issues
mypy mailmap/
# Missing return statements
# Incomplete type hints
# Unused variables
# Etc.
```

---

## 9. Complete Modernized Database Module

Here's the complete `database.py` after all modernizations:

```python
"""SQLite database operations for mailmap (modernized for Python 3.12+)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def serialize_datetime(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO 8601 string for SQLite storage.

    Args:
        dt: datetime object or None

    Returns:
        ISO 8601 formatted string (e.g., "2024-01-15T10:30:00") or None
    """
    if dt is None:
        return None
    return dt.isoformat()


def deserialize_datetime(iso_str: str | None) -> datetime | None:
    """Deserialize ISO 8601 string to datetime object.

    Args:
        iso_str: ISO 8601 formatted string or None

    Returns:
        datetime object or None
    """
    if iso_str is None:
        return None
    return datetime.fromisoformat(iso_str)


@dataclass
class Email:
    """Email record for classification tracking.

    Note: We store mbox_path instead of body_text to save space.
    The original email can be retrieved from the mbox file by message_id.

    Dates are stored as ISO 8601 strings to avoid sqlite3 deprecation warnings.
    """
    message_id: str
    folder_id: str  # Original folder the email came from
    subject: str
    from_addr: str
    mbox_path: str  # Path to mbox file for retrieving original email
    classification: str | None = None
    confidence: float | None = None
    is_spam: bool = False
    spam_reason: str | None = None  # Which rule matched
    processed_at: str | None = None  # ISO 8601 format
    transferred_at: str | None = None  # ISO 8601 format


SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    folder_id TEXT NOT NULL,
    subject TEXT,
    from_addr TEXT,
    mbox_path TEXT,
    classification TEXT,
    confidence REAL,
    is_spam INTEGER DEFAULT 0,
    spam_reason TEXT,
    processed_at TEXT,
    transferred_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder_id);
CREATE INDEX IF NOT EXISTS idx_emails_classification ON emails(classification);
CREATE INDEX IF NOT EXISTS idx_emails_is_spam ON emails(is_spam);
"""


class Database:
    """SQLite database wrapper with context manager support.

    Usage as context manager for automatic setup and cleanup:

        with Database("mailmap.db") as db:
            emails = db.get_unclassified_emails()
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "Database":
        """Context manager entry: connect and initialize schema."""
        self.connect()
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit: close connection."""
        self.close()
        return None

    def connect(self) -> None:
        """Open database connection."""
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get active database connection.

        Raises:
            RuntimeError: If database not connected
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    def init_schema(self) -> None:
        """Initialize database schema with migrations."""
        self.conn.executescript(SCHEMA)

        # Migration: add columns if they don't exist
        cursor = self.conn.execute("PRAGMA table_info(emails)")
        columns = {row["name"] for row in cursor.fetchall()}

        if "is_spam" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN is_spam INTEGER DEFAULT 0")
        if "spam_reason" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN spam_reason TEXT")
        if "transferred_at" not in columns:
            self.conn.execute("ALTER TABLE emails ADD COLUMN transferred_at TEXT")

        self.conn.commit()

    def insert_email(self, email: Email) -> None:
        """Insert or replace an email record."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO emails
            (message_id, folder_id, subject, from_addr, mbox_path,
             classification, confidence, is_spam, spam_reason, processed_at,
             transferred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email.message_id,
                email.folder_id,
                email.subject,
                email.from_addr,
                email.mbox_path,
                email.classification,
                email.confidence,
                1 if email.is_spam else 0,
                email.spam_reason,
                email.processed_at,  # Already ISO 8601 string
                email.transferred_at,  # Already ISO 8601 string
            ),
        )
        self.conn.commit()

    def get_email(self, message_id: str) -> Email | None:
        """Get an email by message ID."""
        row = self.conn.execute(
            "SELECT * FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row:
            return self._row_to_email(row)
        return None

    def _row_to_email(self, row: sqlite3.Row) -> Email:
        """Convert database row to Email object."""
        return Email(
            message_id=row["message_id"],
            folder_id=row["folder_id"],
            subject=row["subject"],
            from_addr=row["from_addr"],
            mbox_path=row["mbox_path"],
            classification=row["classification"],
            confidence=row["confidence"],
            is_spam=bool(row["is_spam"]) if row["is_spam"] is not None else False,
            spam_reason=row["spam_reason"],
            processed_at=row["processed_at"],
            transferred_at=row.get("transferred_at") if "transferred_at" in row.keys() else None,
        )

    def update_classification(
        self, message_id: str, classification: str, confidence: float
    ) -> None:
        """Update the classification for an email."""
        self.conn.execute(
            """
            UPDATE emails
            SET classification = ?, confidence = ?, processed_at = ?
            WHERE message_id = ?
            """,
            (classification, confidence, serialize_datetime(datetime.now()), message_id),
        )
        self.conn.commit()

    def mark_as_spam(self, message_id: str, reason: str) -> None:
        """Mark an email as spam with the matching rule."""
        self.conn.execute(
            """
            UPDATE emails
            SET is_spam = 1, spam_reason = ?, classification = 'Spam'
            WHERE message_id = ?
            """,
            (reason, message_id),
        )
        self.conn.commit()

    def mark_as_transferred(self, message_id: str) -> None:
        """Mark an email as successfully transferred to target folder."""
        self.conn.execute(
            """
            UPDATE emails
            SET transferred_at = ?
            WHERE message_id = ?
            """,
            (serialize_datetime(datetime.now()), message_id),
        )
        self.conn.commit()

    # ... rest of methods unchanged (query-only) ...
```

---

## Testing Strategy

```python
# tests/test_modernization.py
"""Tests for Python 3.12 modernizations."""

import pytest
from datetime import datetime
from mailmap.database import Database, Email, serialize_datetime, deserialize_datetime
from mailmap.types import AttachmentInfo, EmailSummary
from mailmap.imap_client import ImapMailbox
from unittest.mock import MagicMock, patch


def test_datetime_serialization():
    """Test ISO 8601 datetime serialization."""
    dt = datetime(2024, 1, 15, 10, 30, 0)
    assert serialize_datetime(dt) == "2024-01-15T10:30:00"
    assert serialize_datetime(None) is None

    # Round trip
    iso = serialize_datetime(dt)
    assert deserialize_datetime(iso) == dt


def test_database_with_context_manager(tmp_path):
    """Test Database context manager properly initializes."""
    db_path = tmp_path / "test.db"

    with Database(db_path) as db:
        assert db._conn is not None
        # Schema initialized
        cursor = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "emails" in tables

    # After exit, connection closed
    assert db._conn is None


def test_attachment_info_typed_dict():
    """Test AttachmentInfo TypedDict."""
    att: AttachmentInfo = {
        "filename": "document.pdf",
        "content_type": "application/pdf",
    }
    assert att["filename"] == "document.pdf"

    # With optional text_content
    att2: AttachmentInfo = {
        "filename": "notes.txt",
        "content_type": "text/plain",
        "text_content": "Some notes",
    }
    assert att2.get("text_content") == "Some notes"


@patch('mailmap.imap_client.IMAPClient')
def test_imap_mailbox_context_manager(mock_client):
    """Test ImapMailbox context manager."""
    config = MagicMock()
    config.host = "example.com"
    config.port = 993

    with ImapMailbox(config) as mailbox:
        assert mailbox._client is not None

    # Verify disconnect was called
    assert mailbox._client is None


def test_email_dataclass_iso_datetime():
    """Test Email dataclass with ISO 8601 dates."""
    email = Email(
        message_id="test@example.com",
        folder_id="INBOX",
        subject="Test",
        from_addr="user@example.com",
        mbox_path="/path/to/mbox",
        processed_at="2024-01-15T10:30:00",
    )
    assert email.processed_at == "2024-01-15T10:30:00"


def test_database_datetime_roundtrip(tmp_path):
    """Test inserting and retrieving emails with datetime."""
    db_path = tmp_path / "test.db"

    with Database(db_path) as db:
        now = datetime.now()
        email = Email(
            message_id="test@example.com",
            folder_id="INBOX",
            subject="Test",
            from_addr="user@example.com",
            mbox_path="/path",
            processed_at=serialize_datetime(now),
        )
        db.insert_email(email)

        retrieved = db.get_email("test@example.com")
        assert retrieved is not None
        assert retrieved.processed_at == serialize_datetime(now)
```

---

## Summary Checklist

- [ ] Create `mailmap/types.py` with TypedDict definitions
- [ ] Create `mailmap/protocols.py` with Protocol definitions
- [ ] Update `mailmap/database.py` with datetime serialization
- [ ] Add context manager to `ImapMailbox`
- [ ] Add `frozen=True` to `ClassificationResult`, `FolderDescription`, `SuggestedFolder`
- [ ] Add `slots=True` to `EmailMessage`
- [ ] Run `ruff check --fix mailmap/` to modernize union types
- [ ] Update `pyproject.toml` with Python 3.12 and stricter mypy
- [ ] Add match statements to `spam.py` and `content.py` (optional)
- [ ] Add tests for all new features
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Run type checker: `mypy mailmap/`
- [ ] Deploy to staging for integration testing

