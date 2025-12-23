# Modernization Implementation Checklist

Quick reference for implementing all recommendations in priority order.

---

## Phase 1: Critical (Prevent Python 3.13+ Breakage)

### 1.1 Create type definitions file
- [ ] Create `/home/john/src/mailmap/mailmap/types.py`
- [ ] Add `AttachmentInfo`, `EmailSummary`, `LLMResponse` TypedDicts
- [ ] Verify imports work: `python -c "from mailmap.types import AttachmentInfo"`

**Time: 15 min | Files: 1 new**

### 1.2 Fix SQLite datetime issue
- [ ] In `mailmap/database.py`:
  - [ ] Add `serialize_datetime()` and `deserialize_datetime()` helper functions
  - [ ] Update `Email` dataclass: `processed_at: str | None` (was `datetime | None`)
  - [ ] Update `Email` dataclass: `transferred_at: str | None` (was `datetime | None`)
  - [ ] Update `insert_email()` to pass string (use `serialize_datetime(datetime.now())`)
  - [ ] Update `update_classification()` to use `serialize_datetime()`
  - [ ] Update `mark_as_transferred()` to use `serialize_datetime()`
  - [ ] Update `_row_to_email()` to return strings as-is (no conversion)
- [ ] Verify: `pytest tests/test_database.py -v`

**Time: 45 min | Files: 1 modified**

### 1.3 Update Python version requirement
- [ ] In `pyproject.toml`:
  - [ ] Change `requires-python = ">=3.11"` to `requires-python = ">=3.12"`
  - [ ] Change `python_version = "3.11"` to `python_version = "3.12"` in mypy config
- [ ] Verify: `python --version` shows 3.12+

**Time: 5 min | Files: 1 modified**

### 1.4 Clean up type imports (automated)
- [ ] Run: `ruff check --fix --select UP mailmap/ tests/`
  - This converts `Optional[X]` → `X | None`
  - Converts `List[X]` → `list[X]`
  - Removes unused imports
- [ ] Verify: `git diff` shows only Union type changes
- [ ] Run tests: `pytest tests/ -v`

**Time: 10 min | No manual changes**

**Phase 1 Total: ~75 min | Files changed: 2**

---

## Phase 2: Modernize Patterns

### 2.1 Create Protocol definitions
- [ ] Create `/home/john/src/mailmap/mailmap/protocols.py`
- [ ] Add `EmailSource(Protocol)` with `get_emails()` and `close()` methods
- [ ] Add `EmailTarget(Protocol)` with `send_email()` and `create_folder()` methods
- [ ] Mark as `@runtime_checkable` for dynamic type checking

**Time: 20 min | Files: 1 new**

### 2.2 Add context managers to ImapMailbox
- [ ] In `mailmap/imap_client.py`:
  - [ ] Add `__enter__(self) -> ImapMailbox:` method that calls `connect()`
  - [ ] Add `__exit__(self, exc_type, exc_val, exc_tb)` method that calls `disconnect()`
  - [ ] Update docstring to document context manager support
- [ ] Find and update 3-4 usages:
  - [ ] In `_run_idle_loop()` - change from try/finally to with statement
  - [ ] In `_check_folder_once()` - change to with statement
  - [ ] Any other manual connect/disconnect pairs
- [ ] Verify: `pytest tests/test_imap_client.py -v`

**Time: 30 min | Files: 1 modified**

### 2.3 Add frozen dataclasses
- [ ] In `mailmap/llm.py`:
  - [ ] Add `frozen=True` to `@dataclass` on `ClassificationResult`
  - [ ] Change `secondary_labels: list[str]` to `secondary_labels: tuple[str, ...]`
  - [ ] Add `frozen=True` to `FolderDescription`
  - [ ] Add `frozen=True` to `SuggestedFolder`
- [ ] In `mailmap/imap_client.py`:
  - [ ] Add `slots=True` to `EmailMessage` (for memory efficiency)
- [ ] Update any code creating these objects (shouldn't need to, dataclasses handle it)
- [ ] Verify: `pytest tests/ -v`

**Time: 25 min | Files: 2 modified**

### 2.4 Update type hints in core files
- [ ] In `mailmap/llm.py`:
  - [ ] Change `attachments: list[dict] | None` → `attachments: list[AttachmentInfo] | None`
  - [ ] Add return type hint to `_parse_json()`: `-> dict | list | None`
  - [ ] Change `_format_email_samples()` to use `list[dict[str, str]]` parameter type
- [ ] In `mailmap/imap_client.py`:
  - [ ] Update `extract_attachments()` return type: `-> list[AttachmentInfo]`
  - [ ] Update internal dict assignments to use typed dicts
- [ ] In `mailmap/content.py`:
  - [ ] Update `extract_email_summary()` return type: `-> EmailSummary`
- [ ] Run mypy: `mypy mailmap/`

**Time: 30 min | Files: 3 modified**

### 2.5 Improve mypy configuration
- [ ] In `pyproject.toml`:
  - [ ] Update mypy section:
    ```toml
    [tool.mypy]
    python_version = "3.12"
    warn_unused_ignores = true
    warn_redundant_casts = true
    warn_unused_configs = true
    check_untyped_defs = true
    disallow_incomplete_defs = true
    no_implicit_optional = true
    warn_no_return = true

    [[tool.mypy.overrides]]
    module = "imapclient.*"
    ignore_missing_imports = true

    [[tool.mypy.overrides]]
    module = "html2text"
    ignore_missing_imports = true
    ```
- [ ] Run mypy: `mypy mailmap/` (may find more issues)
- [ ] Fix any issues found

**Time: 15 min | Files: 1 modified**

**Phase 2 Total: ~120 min | Files changed: 4**

---

## Phase 3: Code Quality (Optional)

### 3.1 Add match statements (optional)
- [ ] In `mailmap/spam.py` check_rule() function:
  - [ ] Replace numeric comparisons `if/elif` with `match` statement
  - [ ] Replace string comparisons `if/elif` with `match` statement
- [ ] In `mailmap/imap_client.py` extract_attachments():
  - [ ] Consider `match content_type:` for parsing logic
- [ ] In `mailmap/content.py`:
  - [ ] Consider `match` for signature detection logic
- [ ] Verify: `pytest tests/ -v`

**Time: 20 min | Files: 3 modified (optional)**

### 3.2 Add documentation
- [ ] In `mailmap/imap_client.py` class docstring:
  - [ ] Document that executor pattern is intentional for blocking I/O
  - [ ] Explain why IMAP isn't async (imapclient library)
- [ ] In protocol classes:
  - [ ] Add examples of implementations
- [ ] In typed dict definitions:
  - [ ] Add examples of construction

**Time: 10 min | Files: 2 modified**

### 3.3 Add missing `__all__` exports
- [ ] In each `mailmap/*.py` file, add at module level:
  ```python
  __all__ = ["ClassName", "function_name", ...]
  ```
- [ ] Run mypy with `--strict` mode to verify

**Time: 10 min | Files: 5-8 modified**

**Phase 3 Total: ~40 min | Files changed: 10+ (optional)**

---

## Testing Strategy

### After Each Phase

```bash
# Phase 1
pytest tests/test_database.py -v
mypy mailmap/database.py

# Phase 2
pytest tests/ -v
mypy mailmap/
ruff check mailmap/  # Check for linting issues

# Phase 3
pytest tests/ -v --cov=mailmap
mypy mailmap/ --strict
```

### Full validation before committing
```bash
# 1. Run all tests
pytest tests/ -v

# 2. Run type checker
mypy mailmap/

# 3. Run linter
ruff check mailmap/

# 4. Check for deprecation warnings
python -W all -m pytest tests/ 2>&1 | grep -i deprecat

# 5. Build and install
pip install -e .

# 6. Basic smoke test
mailmap --help
```

---

## Migration Path for Existing Data

If database already has `datetime` objects stored (unlikely but possible):

```python
# One-time migration script (run after code update)
from mailmap.database import Database, serialize_datetime, deserialize_datetime
from datetime import datetime

def migrate_database(db_path: str) -> None:
    """Convert any legacy datetime objects to ISO 8601 strings."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT message_id, processed_at, transferred_at FROM emails"
    )
    rows = cursor.fetchall()

    for msg_id, processed, transferred in rows:
        # These should already be strings from new code
        # This is just a safety check
        if processed and not isinstance(processed, str):
            processed = serialize_datetime(processed)
        if transferred and not isinstance(transferred, str):
            transferred = serialize_datetime(transferred)

        conn.execute(
            "UPDATE emails SET processed_at=?, transferred_at=? WHERE message_id=?",
            (processed, transferred, msg_id)
        )

    conn.commit()
    conn.close()
    print(f"Migrated {len(rows)} emails")

# Usage
# migrate_database("mailmap.db")
```

---

## Rollback Procedure

If issues arise, each phase can be rolled back:

### Phase 1 Rollback
```bash
# Revert database.py to use datetime objects
git diff mailmap/database.py | less
git checkout mailmap/database.py

# Downgrade Python requirement
git checkout pyproject.toml

# Run old version
pytest tests/test_database.py -v
```

### Phase 2 Rollback
```bash
# Remove context manager methods from ImapMailbox
git diff mailmap/imap_client.py

# Revert frozen dataclasses
git diff mailmap/llm.py

# Full rollback
git checkout mailmap/
```

---

## Deliverables Checklist

After implementation, verify:

- [ ] All tests pass: `pytest tests/ -v`
- [ ] No mypy errors: `mypy mailmap/`
- [ ] No ruff issues: `ruff check mailmap/`
- [ ] No deprecation warnings: `python -W error::DeprecationWarning -m pytest tests/`
- [ ] Documentation updated: MODERNIZATION_REPORT.md has notes
- [ ] Code examples included: MODERNIZATION_EXAMPLES.md reviewed
- [ ] Commit messages clear and atomic
- [ ] PR description references specific issues
- [ ] Backward compatibility maintained (if needed)

---

## Commit Strategy

Recommended atomic commits (one per change):

```bash
# Phase 1
git commit -m "fix: serialize datetime to ISO 8601 for sqlite3 compatibility"
git commit -m "build: require Python 3.12+"
git commit -m "refactor: modernize type hints (Optional -> |, List -> list)"

# Phase 2
git commit -m "feat: add TypedDict and Protocol type definitions"
git commit -m "refactor: add context manager support to ImapMailbox"
git commit -m "refactor: use frozen dataclasses for immutable value objects"
git commit -m "refactor: improve type hints with typed dicts"
git commit -m "build: increase mypy strictness"

# Phase 3 (optional)
git commit -m "refactor: use match statements for cleaner control flow"
git commit -m "docs: add __all__ exports and module docstrings"
```

---

## Timeline Estimate

- **Phase 1 (Critical):** ~75 minutes (can deploy immediately)
- **Phase 2 (High Priority):** ~120 minutes (recommended same sprint)
- **Phase 3 (Nice-to-Have):** ~40 minutes (can defer to next sprint)

**Total:** 3-4 hours spread over 2-3 sprints

**Recommended approach:**
- Sprint 1: Complete Phase 1 + Phase 2 in separate PRs
- Sprint 2: Phase 3 improvements
- Ongoing: Continue using modern patterns for new code

---

## Questions During Implementation?

Refer to:
1. **MODERNIZATION_REPORT.md** - Full analysis and rationale
2. **MODERNIZATION_EXAMPLES.md** - Before/after code examples
3. **This file** - Step-by-step implementation guide

All three documents are checked into the repository at `/home/john/src/mailmap/`

