# Test Coverage - Quick Reference Guide

**Current State:** 36% coverage, 1 test failing, 65 passing

---

## Coverage by Module

```
✅ src/mailmap/config.py              100% (46/46)
✅ src/mailmap/content.py             100% (53/53)
✅ src/mailmap/database.py            100% (71/71)
⚠️  src/mailmap/thunderbird.py         72%  (91/126)
❌ src/mailmap/llm.py                  38%  (93/246)
❌ src/mailmap/imap_client.py          29%  (49/167)
❌ src/mailmap/main.py                  0%  (0/363)
❌ src/mailmap/mcp_server.py            0%  (0/55)
```

---

## What's Tested Well ✅

| Module | What's Tested | Coverage |
|--------|---|---|
| **config.py** | All 4 config classes, TOML loading, defaults | 100% |
| **content.py** | HTML removal, URL replacement, truncation, signature removal, email summary extraction | 100% |
| **database.py** | CRUD for folders/emails, connections, schema | 100% |
| **thunderbird.py** | Profile detection, mbox reading, folder listing | 72% |

---

## What's NOT Tested ❌

### Critical Gaps (High Risk)

| Module | Gap | Lines Missing | Impact |
|--------|-----|---|---|
| **main.py** | Email processor, 3-phase import, CLI modes | 363 (0%) | **CRITICAL** - Core functionality untested |
| **llm.py** | JSON error recovery, fallback logic | 153 (62%) | **CRITICAL** - LLM integration errors not handled |
| **imap_client.py** | Network operations, IDLE listener | 118 (71%) | **CRITICAL** - Real IMAP logic untested |
| **mcp_server.py** | MCP server endpoints | 55 (100%) | **CRITICAL** - AI API untested |

---

## One-Click Test Running

```bash
# Current test suite (65 tests, 1 failure)
python -m pytest tests/ -v

# With coverage report
python -m pytest tests/ --cov=mailmap --cov-report=html

# Just the passing tests
python -m pytest tests/ -k "not test_defaults"

# Just one module
python -m pytest tests/test_content.py -v
```

---

## The One Failing Test (Simple Fix)

**Location:** `tests/test_config.py:43`

**Problem:**
```python
def test_defaults(self):
    config = OllamaConfig()
    assert config.timeout_seconds == 120  # ❌ WRONG
```

**Fix:**
```python
def test_defaults(self):
    config = OllamaConfig()
    assert config.timeout_seconds == 300  # ✅ CORRECT (default in config.py line 23)
```

---

## High-Value Tests to Add (Biggest Impact)

### 1. LLM Error Handling (15 tests, ~300 lines)
**Why:** LLM response parsing has 6+ error paths, none tested

**Key scenarios:**
- Invalid folder fallback (lines 137-140)
- Confidence threshold logic (lines 143-145)
- JSON parse failure recovery (lines 125-134)
- Missing/malformed array responses (lines 217-230)
- JSON repair mechanism (lines 354-371)

### 2. EmailProcessor Pipeline (10 tests, ~250 lines)
**Why:** Queue management and processing loop untested

**Key scenarios:**
- Message queueing and dequeuing
- Email classification with LLM
- Error handling doesn't crash loop
- Database persistence
- Missing folder descriptions handling

### 3. Thunderbird Three-Phase Import (8 tests, ~200 lines)
**Why:** Complex import sequence with 3 phases, all untested

**Key scenarios:**
- Phase 1: Folder sync
- Phase 2: Description generation
- Phase 3: Email classification
- Skip already-imported emails
- Continue on individual email failures

### 4. IMAP Client (12 tests, ~200 lines)
**Why:** Real network operations, completely untested

**Key scenarios:**
- Connection establishment
- Folder enumeration
- Email retrieval and parsing
- Connection error recovery
- IDLE monitoring for new emails

---

## Test File Organization

```
tests/
├── conftest.py                    # Fixtures (already good)
├── test_config.py                 # ✅ 100% coverage
├── test_content.py                # ✅ 100% coverage
├── test_database.py               # ✅ 100% coverage
├── test_thunderbird.py            # ✅ 72% coverage
├── test_llm.py                    # ⚠️  8 tests
├── test_llm_advanced.py           # ❌ MISSING - Add error handling
├── test_main_email_processor.py   # ❌ MISSING - Add queue/processing tests
├── test_main_thunderbird_import.py # ❌ MISSING - Add 3-phase import tests
└── test_imap_client.py            # ❌ MISSING - Add IMAP tests
```

---

## Known Issues

### 1. SQLite Deprecation Warnings (18 total)
**Severity:** Low (warnings only, functionality works)

**Issue:** Python 3.12+ deprecated built-in datetime adapters

**Files affected:** `database.py` lines 88, 134, 173

**Solution:** Register custom datetime adapters with sqlite3 module

### 2. Test Isolation Issue
**Severity:** Low

**Issue:** Tests use `/tmp/venv_mailmap` which is temporary

**Solution:** Use pytest fixtures with `tmp_path` (already done in conftest.py)

---

## Coverage Goals by Phase

| Phase | Target | Timeline | Tests to Add |
|-------|--------|----------|---|
| Phase 1 (Now) | Fix failing test, add critical LLM/main tests | This week | 15-20 |
| Phase 2 | Reach 60% coverage | 2 weeks | 30-40 |
| Phase 3 | Reach 75% coverage | 4 weeks | 50-65 |
| Phase 4 | Reach 85% coverage | 8 weeks | 100+ |

---

## Mocking Strategy

### For LLM Module
```python
# Mock the HTTP client
with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": json.dumps({...})}
    mock_post.return_value = mock_resp
```

### For IMAP Client
```python
# Mock imapclient.IMAPClient
with patch("mailmap.imap_client.IMAPClient") as mock_imap:
    mock_imap.return_value.list_folders.return_value = [...]
```

### For Database
```python
# Use temp database (conftest.py already does this)
@pytest.fixture
def test_db(temp_dir):
    db = Database(temp_dir / "test.db")
    db.connect()
    db.init_schema()
    yield db
    db.close()
```

---

## Quick Win: Fix Test in 30 Seconds

```bash
# Edit the file
vi tests/test_config.py

# Find line 43, change:
# assert config.timeout_seconds == 120
# To:
# assert config.timeout_seconds == 300

# Save and verify
python -m pytest tests/test_config.py::TestOllamaConfig::test_defaults -v
# Should pass!
```

---

## Top 5 Things to Test

### 1. LLM Response Validation (Lines 125-150 in llm.py)
```
Impact: Medium | Effort: Low | Value: High
```
Invalid folders, missing fields, low confidence

### 2. EmailProcessor Error Handling (Lines 36-45 in main.py)
```
Impact: High | Effort: Low | Value: Very High
```
Queue processing, error recovery, database state

### 3. Thunderbird Import Phases (Lines 198-314 in main.py)
```
Impact: High | Effort: Medium | Value: Very High
```
Folder sync, description generation, email classification

### 4. IMAP Connection Management (Lines 40-100 in imap_client.py)
```
Impact: Medium | Effort: Medium | Value: High
```
Connect, disconnect, error recovery

### 5. MCP Server Endpoints (All of mcp_server.py)
```
Impact: Medium | Effort: Medium | Value: High
```
Tool registration, request handling, error responses

---

## Recommended Next Steps

1. **Today:** Fix the 1 failing test (30 seconds)
2. **This week:** Add 15 LLM error handling tests (~4 hours)
3. **Next week:** Add 10 EmailProcessor tests (~6 hours)
4. **Following week:** Add 8 Thunderbird import tests (~5 hours)

**Total effort:** ~50-60 hours for one engineer to reach 75% coverage

---

## See Also

- `TEST_COVERAGE_ANALYSIS.md` - Detailed coverage analysis with examples
- `RECOMMENDED_TESTS.md` - Complete test code ready to implement

