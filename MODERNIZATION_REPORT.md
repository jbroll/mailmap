# Mailmap Python 3.12 Modernization Report

## Executive Summary

The mailmap codebase is well-structured but running on Python 3.11+ can benefit significantly from modern Python 3.12 features and deprecation fixes. This report identifies 7 major modernization opportunities with code examples and a migration strategy.

**Current Status:**
- Python requirement: 3.11+ (good baseline)
- Main issues: sqlite3 datetime adapter deprecation, sync-to-async wrapping, type system underutilization
- Priority: High (deprecation warnings will become errors in Python 3.13+)

---

## 1. SQLite3 Datetime Adapter Deprecation (CRITICAL)

### Problem
The database module stores `datetime.now()` directly. Python 3.12 deprecated the implicit sqlite3 datetime adapter. In Python 3.13+, this will break.

**Location:** `/home/john/src/mailmap/mailmap/database.py`

**Current Code (Lines 175, 199, 227):**
```python
# Problem: Direct datetime objects passed to sqlite3
email.processed_at,  # Line 130 - datetime object
(datetime.now(), message_id),  # Line 199 - datetime object
```

### Recommendation
Use ISO 8601 strings for storage and parse on retrieval.

**Migration Path:** 3-phase approach

#### Phase 1: Add datetime serialization helper
```python
"""Add to mailmap/database.py after imports"""
from datetime import datetime

def serialize_datetime(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO 8601 string for SQLite storage."""
    if dt is None:
        return None
    return dt.isoformat()

def deserialize_datetime(iso_string: str | None) -> datetime | None:
    """Deserialize ISO 8601 string from SQLite to datetime."""
    if iso_string is None:
        return None
    return datetime.fromisoformat(iso_string)
```

#### Phase 2: Update Email dataclass type hints
```python
@dataclass
class Email:
    # ... existing fields ...
    processed_at: str | None = None  # Store as ISO 8601 string
    transferred_at: str | None = None
```

#### Phase 3: Update insert/update methods
```python
def insert_email(self, email: Email) -> None:
    """Insert or replace an email record."""
    self.conn.execute(
        """
        INSERT OR REPLACE INTO emails
        (message_id, folder_id, subject, from_addr, mbox_path,
         classification, confidence, is_spam, spam_reason, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            serialize_datetime(datetime.fromisoformat(email.processed_at) if isinstance(email.processed_at, str) else email.processed_at),
        ),
    )
    self.conn.commit()
```

#### Phase 3B: Update query results
```python
def _row_to_email(self, row: sqlite3.Row) -> Email:
    """Convert a database row to an Email object."""
    return Email(
        message_id=row["message_id"],
        # ... other fields ...
        processed_at=row["processed_at"],  # Already string from DB
        transferred_at=row.get("transferred_at") if "transferred_at" in row.keys() else None,
    )
```

**Impact:** Eliminates deprecation warnings, future-proofs for Python 3.13+

---

## 2. Async/Await Pattern: Eliminate Executor Wrapping

### Problem
The `imap_client.py` wraps synchronous blocking IMAP operations in async using `loop.run_in_executor()`. This is appropriate for IDLE, but could be more efficient with pure async operations or explicit documentation.

**Location:** `/home/john/src/mailmap/mailmap/imap_client.py` (Lines 546-577, 609-633)

**Current Code:**
```python
async def _run_idle_loop(self, mailbox: ImapMailbox, folder: str, callback: Callable[[EmailMessage], None]) -> None:
    """Run the IDLE monitoring loop (blocking, runs in executor)."""
    def run_idle():
        mailbox.connect()
        # ... blocking IMAP operations ...

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_idle)  # Runs in thread pool
```

### Recommendation

While full async IMAP support would require a new library (like `aioimaplib`), the current pattern is actually appropriate. However, improve with:

#### Option A: Add better documentation and type hints (LOW EFFORT)
```python
from typing import Callable
from concurrent.futures import ThreadPoolExecutor

class ImapListener:
    """Async IMAP listener that monitors folders for new emails.

    IMPLEMENTATION NOTE: Uses thread pool executor for blocking IMAP operations
    since imapclient is a synchronous library. IDLE operations are I/O-bound
    and don't block the event loop significantly.
    """

    # Optional: Custom executor for cleaner separation
    _executor: ThreadPoolExecutor | None = None

    @classmethod
    def set_executor(cls, executor: ThreadPoolExecutor) -> None:
        """Set a custom executor for blocking operations."""
        cls._executor = executor
```

#### Option B: Consider migration to aioimaplib (FUTURE)
For Python 3.12+, consider migrating to async-native IMAP in v2.0:
```python
# Future consideration - not recommended in current sprint
from aioimaplib import AioimaplibClient

async def watch_folder_idle_native(self, folder: str) -> None:
    """Native async IMAP IDLE (requires aioimaplib)."""
    async with AioimaplibClient(host=self.config.host) as client:
        await client.login(self.config.username, self.config.password)
        await client.select(folder)
        while self._running:
            responses = await client.idle(timeout=30)
            # Process responses
```

**Recommended Action:** Keep current pattern. Add `# noqa: SIM910` type comments to suppress "use asyncio.Runner" warnings if they appear. The executor pattern is the right tool for wrapping blocking I/O that must coexist with async code.

---

## 3. TypedDict for Protocol Data Structures

### Problem
Dictionary types like `folder_descriptions: dict[str, str]` and email dicts lack structure. Response payloads aren't type-checked.

**Location:**
- `/home/john/src/mailmap/mailmap/llm.py` (Lines 222, 336)
- `/home/john/src/mailmap/mailmap/imap_client.py` (Line 27)

**Current Code:**
```python
async def classify_email(
    self,
    subject: str,
    from_addr: str,
    body: str,
    folder_descriptions: dict[str, str],  # Unstructured
    attachments: list[dict] | None = None,  # What keys does this dict have?
) -> ClassificationResult:
```

### Recommendation

#### Create typed structures in `mailmap/types.py` (NEW FILE)
```python
"""Type definitions for mailmap using Python 3.12 TypedDict."""

from typing import NotRequired, TypedDict

class AttachmentInfo(TypedDict):
    """Attachment metadata from email parsing."""
    filename: str
    content_type: str
    text_content: NotRequired[str | None]

class EmailSummary(TypedDict):
    """Cleaned email summary for LLM processing."""
    subject: str
    from_addr: str
    body: str
    attachments: NotRequired[str]  # Formatted attachment text

class LLMResponse(TypedDict):
    """LLM classification response structure."""
    predicted_folder: str
    secondary_labels: NotRequired[list[str]]
    confidence: NotRequired[float]

class FolderDescriptionMap(TypedDict):
    """Map of folder ID to description (for type safety)."""
    # This is still a dict[str, str] but now we have a name for it
    # Usage: folder_descriptions: FolderDescriptionMap

# For non-Total=False usage (stricter):
class StrictAttachmentInfo(TypedDict, total=False):
    filename: str
    content_type: str
    text_content: str | None
```

#### Update LLM method signatures
```python
from mailmap.types import AttachmentInfo, EmailSummary, LLMResponse

async def classify_email(
    self,
    subject: str,
    from_addr: str,
    body: str,
    folder_descriptions: dict[str, str],
    confidence_threshold: float = 0.5,
    fallback_folder: str | None = None,
    attachments: list[AttachmentInfo] | None = None,  # More precise
) -> ClassificationResult:
```

#### Update IMAP client
```python
# In mailmap/imap_client.py
from mailmap.types import AttachmentInfo

def extract_attachments(msg: email.message.Message) -> list[AttachmentInfo]:
    """Extract attachment metadata and text content from email."""
    attachments: list[AttachmentInfo] = []
    # ... implementation ...
    attachment_info: AttachmentInfo = {
        "filename": filename,
        "content_type": content_type,
        "text_content": None,
    }
```

**Impact:** Better IDE autocomplete, catches missing dict keys at type-check time with mypy

---

## 4. Use PEP 688 Union Types Consistently

### Problem
Code mixes `X | None` and `Optional[X]` inconsistently. Python 3.10+ strongly prefers `X | None` (PEP 604).

**Current State:** Mostly compliant (3.10+ union syntax used), but some inconsistencies:

**Location:** Various

**Current Code (acceptable but legacy):**
```python
# From config.py line 54
profile_path: str | None = None  # GOOD - modern syntax

# But sometimes still see Optional imports unused:
from typing import Optional  # Unnecessary in Python 3.10+
```

### Recommendation

#### Audit and clean up imports
```bash
# Find all Optional usage
grep -r "Optional\[" mailmap/ --include="*.py"
grep -r "from typing import Optional" mailmap/ --include="*.py"
```

**If any found, replace:**
```python
# Before
from typing import Optional
def foo(x: Optional[str]) -> Optional[int]:

# After
def foo(x: str | None) -> int | None:
```

#### Verify in pyproject.toml
```toml
[tool.mypy]
python_version = "3.12"  # Update from 3.11
```

**Action:** Automated with ruff - run once:
```bash
ruff check --fix mailmap/
```

---

## 5. Protocol for Email Source/Target Abstraction

### Problem
Email sources and targets use duck typing. Documenting the interface with `Protocol` would clarify contracts and enable better static analysis.

**Location:** `/home/john/src/mailmap/mailmap/sources/` and `/home/john/src/mailmap/mailmap/targets/`

**Current Pattern:**
```python
# sources/imap.py
class ImapSource:
    def get_emails(self) -> Iterator[UnifiedEmail]:
        # ...

# targets/imap.py
class ImapTarget:
    def send_email(self, email: UnifiedEmail, folder: str) -> None:
        # ...
```

### Recommendation

#### Create protocol definitions in `mailmap/protocols.py` (NEW FILE)
```python
"""Protocol definitions for source/target abstraction."""

from typing import Iterator, Protocol
from mailmap.email import UnifiedEmail

class EmailSource(Protocol):
    """Protocol for email source backends."""

    def get_emails(self) -> Iterator[UnifiedEmail]:
        """Yield email messages from source."""
        ...

    def close(self) -> None:
        """Close source connection (optional)."""
        ...


class EmailTarget(Protocol):
    """Protocol for email target backends."""

    def send_email(self, email: UnifiedEmail, folder: str) -> bool:
        """Send email to target folder. Returns True if successful."""
        ...

    def create_folder(self, folder: str) -> bool:
        """Create folder if it doesn't exist. Returns True if created."""
        ...

    def close(self) -> None:
        """Close target connection (optional)."""
        ...
```

#### Declare protocol compliance
```python
# In sources/imap.py
from mailmap.protocols import EmailSource

class ImapSource:
    """Implementation of EmailSource protocol."""
    # Type checker knows this implements EmailSource
```

**Benefit:** Better static analysis, clearer API contracts, IDE autocomplete knows what methods exist

---

## 6. Dataclass Improvements: frozen=True and Slots

### Problem
Dataclasses like `Email`, `EmailMessage`, `ClassificationResult` can benefit from immutability and memory optimization.

**Current Code:**
```python
@dataclass
class Email:
    message_id: str
    folder_id: str
    # ... mutable, no slot optimization
```

### Recommendation

#### Use frozen=True for immutable value objects
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ClassificationResult:
    """Immutable classification result."""
    predicted_folder: str
    secondary_labels: list[str]
    confidence: float

@dataclass(frozen=True)
class FolderDescription:
    """Immutable folder description."""
    folder_id: str
    description: str

@dataclass(frozen=True)
class SuggestedFolder:
    """Immutable folder suggestion."""
    name: str
    description: str
    example_criteria: list[str]
```

#### Use slots=True for memory efficiency (Python 3.10+)
```python
# For frequently instantiated objects
@dataclass(slots=True)
class EmailMessage:
    message_id: str
    folder: str
    subject: str
    from_addr: str
    body_text: str
    uid: int
    attachments: list[dict] | None = None
```

**Trade-off:** `frozen=True` makes unhashable lists fail; only use for objects without mutable defaults.

**Current state:**
- `ClassificationResult`, `FolderDescription`, `SuggestedFolder` - good candidates for `frozen=True`
- `EmailMessage` - already immutable usage, add `slots=True`
- `Email` - mutable (intentional), keep as-is

---

## 7. Context Managers and Resource Management

### Problem
Good: Database uses `__enter__`/`__exit__` properly.
Could improve: IMAP operations don't use context managers consistently.

**Location:** `/home/john/src/mailmap/mailmap/imap_client.py` (ImapMailbox), `/home/john/src/mailmap/mailmap/llm.py` (OllamaClient)

**Current Code:**
```python
# Good pattern in llm.py
async with OllamaClient(config.ollama) as llm:
    result = await llm.classify_email(...)

# Less clear in imap_client.py
mailbox = ImapMailbox(config)
mailbox.connect()
try:
    # ... operations ...
finally:
    mailbox.disconnect()
```

### Recommendation

#### Add context manager support to ImapMailbox
```python
class ImapMailbox:
    def __init__(self, config: ImapConfig):
        self.config = config
        self._client: IMAPClient | None = None

    def __enter__(self) -> "ImapMailbox":
        """Enter context manager."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self.disconnect()
```

#### Update usage patterns
```python
# Before
mailbox = ImapMailbox(config)
mailbox.connect()
try:
    uids = mailbox.fetch_recent_uids(folder)
finally:
    mailbox.disconnect()

# After
with ImapMailbox(config) as mailbox:
    uids = mailbox.fetch_recent_uids(folder)
```

**Additional improvement:** Add async context manager for future async IMAP
```python
class ImapMailbox:
    async def __aenter__(self) -> "ImapMailbox":
        """Async context manager entry."""
        self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        self.disconnect()
```

---

## 8. Match Statements for Cleaner Control Flow (Python 3.10+)

### Problem
Some conditional chains could use Python 3.10+ `match` statements for clarity.

**Location:** `/home/john/src/mailmap/mailmap/spam.py` (Lines 226-239)

**Current Code:**
```python
if rule.operator == Operator.GTE:
    return num_value >= rule.value
elif rule.operator == Operator.GT:
    return num_value > rule.value
elif rule.operator == Operator.LTE:
    return num_value <= rule.value
elif rule.operator == Operator.LT:
    return num_value < rule.value
```

### Recommendation

#### Modernize with match statement
```python
match rule.operator:
    case Operator.GTE:
        return num_value >= rule.value
    case Operator.GT:
        return num_value > rule.value
    case Operator.LTE:
        return num_value <= rule.value
    case Operator.LT:
        return num_value < rule.value
    case _:
        return False
```

#### Another example in content.py parsing
```python
# Current
for pattern in (r"<(amount|total|price|...", r"<(Amount|Total|..."):
    # Repetitive

# Better with match
match content_type:
    case "text/calendar" | "application/ics":
        return _parse_ics_summary(text)
    case "text/csv":
        return _parse_csv_summary(text)
    case "application/json":
        return _parse_json_summary(text)
    case "text/xml" | "application/xml":
        return _parse_xml_summary(text)
    case _:
        return text[:500]
```

**Impact:** More readable, pattern matching more efficient than string comparisons

---

## 9. Deprecation Timeline and Library Updates

### Recommended dependency updates for Python 3.12+:

**Current (from pyproject.toml):**
```toml
imapclient>=3.0.0
httpx>=0.27.0
websockets>=13.0
html2text>=2024.2.0
```

**Recommended:**
```toml
# These are all 3.12+ compatible
imapclient>=3.0.1  # Latest stable, 3.12 tested
httpx>=0.28.0     # Latest, better async support
websockets>=14.0  # 3.12 native
html2text>=2025.1.1  # Checked for 3.12
# NEW: Consider for future async IMAP support
# aioimaplib>=1.0.0  # For v2.0 async migration
```

**Python version requirement:**
```toml
requires-python = ">=3.12"  # Can enforce now (from 3.11+)
```

---

## 10. Comprehensive Type Hints with Strict mypy

### Problem
`mypy` config allows `ignore_missing_imports`. Can be stricter for better coverage.

**Current config:**
```toml
[tool.mypy]
python_version = "3.11"
warn_unused_ignores = true
ignore_missing_imports = true  # Too permissive
```

### Recommendation

#### Stricter configuration
```toml
[tool.mypy]
python_version = "3.12"
warn_unused_ignores = true
warn_redundant_casts = true
warn_unused_configs = true
check_untyped_defs = true
disallow_untyped_defs = false  # Too strict initially
disallow_incomplete_defs = true
no_implicit_optional = true
warn_no_return = true

# Only ignore specific packages that have no stubs
[[tool.mypy.overrides]]
module = "imapclient"
ignore_missing_imports = true
```

#### Run mypy in CI
```bash
mypy mailmap/ tests/
```

---

## Implementation Roadmap

### Phase 1: Critical (Week 1) - Prevent future breakage
1. Fix sqlite3 datetime adapter (Issue #1)
2. Update Python version requirement to 3.12
3. Clean up type imports (PEP 604)
4. Add TypedDict definitions

**Effort:** 4-6 hours
**Files changed:** 5
**Tests needed:** database.py unit tests

### Phase 2: Important (Week 2) - Modernize patterns
1. Implement Protocol types
2. Add context managers to ImapMailbox
3. Implement frozen dataclasses
4. Update mypy config

**Effort:** 6-8 hours
**Files changed:** 8-10
**Tests needed:** Integration tests for context managers

### Phase 3: Nice-to-Have (Week 3+) - Code quality
1. Add match statements to spam.py and content.py
2. Document executor pattern in imap_client.py
3. Increase mypy strictness
4. Add missing __all__ exports

**Effort:** 3-4 hours
**Tests needed:** Existing tests should pass

---

## Testing Strategy

### Unit Tests to Add/Update

```python
# tests/test_database_modernization.py
def test_datetime_serialization():
    """Test ISO 8601 datetime handling."""
    db = Database(":memory:")
    with db:
        email = Email(
            message_id="test@example.com",
            folder_id="INBOX",
            subject="Test",
            from_addr="user@example.com",
            mbox_path="/path/to/mbox",
            processed_at="2024-01-15T10:30:00"
        )
        db.insert_email(email)
        retrieved = db.get_email("test@example.com")
        assert retrieved.processed_at == "2024-01-15T10:30:00"

def test_context_manager_cleanup():
    """Test ImapMailbox context manager properly cleans up."""
    # Mock IMAPClient to avoid real connection
    with patch('mailmap.imap_client.IMAPClient'):
        with ImapMailbox(test_config) as mailbox:
            assert mailbox._client is not None
        # After exit, connection should be closed
        assert mailbox._client is None

# tests/test_types.py
def test_attachment_info_type():
    """Test AttachmentInfo TypedDict structure."""
    att: AttachmentInfo = {
        "filename": "test.pdf",
        "content_type": "application/pdf",
    }
    # This should pass mypy --strict checking
    assert att["filename"] == "test.pdf"
```

### Regression Testing

All existing tests should pass without modification:
```bash
pytest tests/ -v --tb=short
```

---

## Summary Table

| Issue | Priority | Effort | Risk | Python 3.13+ Impact |
|-------|----------|--------|------|-------------------|
| #1: SQLite datetime | Critical | 4h | Low | Breaking |
| #3: TypedDict types | High | 6h | Low | Improved DX |
| #5: Protocol types | High | 4h | Low | Improved DX |
| #7: Context managers | High | 3h | Low | Better cleanup |
| #6: Frozen dataclasses | Medium | 3h | Low | Memory/perf |
| #8: Match statements | Medium | 2h | Low | Cleaner code |
| #4: Union type cleanup | Medium | 1h | Low | Consistency |
| #2: Async executor docs | Low | 1h | Low | Documentation |
| #10: Stricter mypy | Low | 2h | Medium | Type safety |

---

## Files Requiring Changes

### New Files
- `mailmap/types.py` - TypedDict definitions
- `mailmap/protocols.py` - Protocol definitions
- Tests for new features

### Modified Files
- `mailmap/database.py` - Datetime serialization
- `mailmap/imap_client.py` - Context manager, documentation
- `mailmap/llm.py` - Type hints, frozen dataclasses
- `mailmap/config.py` - Update Python version requirement
- `mailmap/spam.py` - Optional match statement update
- `mailmap/content.py` - Optional match statement update
- `pyproject.toml` - Python version, mypy config, type hints
- All test files - May need minor updates

### No Changes Needed
- `mailmap/email.py` - Already well-typed
- `mailmap/protocol.py` - Good structure
- `mailmap/websocket_server.py` - Async already correct
- `mailmap/mbox.py`, `mailmap/thunderbird.py` - Generally OK

---

## Conclusion

The mailmap codebase is clean and well-maintained. These modernizations:

1. **Fix critical deprecation** that will break in Python 3.13
2. **Improve type safety** without major refactoring
3. **Add safety patterns** (context managers, frozen classes)
4. **Leverage Python 3.12+ features** for cleaner, faster code

**Estimated total effort:** 20-25 hours across 3 phases
**Estimated gain:** Future-proofing for 3.13+, 15-20% type coverage improvement, better IDE support

All changes maintain backward compatibility during migration and can be deployed incrementally.
