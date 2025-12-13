# Mailmap Test Coverage Analysis

**Date:** December 12, 2025
**Project:** mailmap (Email classification system using local LLM via MCP)
**Current Status:** 1 test failure, 65 passing (98.5% pass rate)
**Overall Code Coverage:** 36% (724 missing statements / 1128 total)

---

## Executive Summary

The mailmap project has a **strong foundation of tests for utility modules** but **critical gaps in integration and orchestration logic**. The main business logic pathways—LLM response parsing, batch processing, and database operations with error handling—lack comprehensive test coverage.

### Coverage by Module

| Module | Coverage | Status | Priority |
|--------|----------|--------|----------|
| `config.py` | 100% | Complete | N/A |
| `content.py` | 100% | Complete | N/A |
| `database.py` | 100% | Complete | N/A |
| `thunderbird.py` | 72% | Good | Low |
| `llm.py` | 38% | **Inadequate** | **CRITICAL** |
| `imap_client.py` | 29% | **Very Poor** | **CRITICAL** |
| `main.py` | 0% | **Missing** | **CRITICAL** |
| `mcp_server.py` | 0% | **Missing** | **CRITICAL** |

---

## Detailed Coverage Analysis

### 1. LLM Module (38% coverage) - CRITICAL GAPS

**File:** `/home/john/src/mailmap/src/mailmap/llm.py` (246 statements, 153 missing)

#### Currently Tested:
- `ClassificationResult` dataclass ✓
- `FolderDescription` dataclass ✓
- `OllamaClient` context manager initialization ✓
- Basic email classification with valid JSON response ✓
- JSON parsing with malformed response fallback ✓
- JSON embedded in text extraction ✓
- `generate_folder_description()` ✓

#### Critical Gaps Identified:

**1. LLM Response Error Handling (Lines 133-150)**
- No tests for invalid folder in classification response
- No tests for confidence threshold logic triggering fallback
- No tests for edge cases: negative confidence, extremely high confidence
- No tests for None/empty folder descriptions

**Missing Test Cases:**
```python
# Test invalid folder handling
async def test_classify_email_invalid_folder():
    """Test that invalid LLM folder response uses fallback"""
    mock_response = {
        "response": json.dumps({
            "predicted_folder": "NonexistentFolder",
            "secondary_labels": [],
            "confidence": 0.95,
        })
    }
    # Should fallback to MiscellaneousAndUncategorized or first folder

# Test confidence threshold
async def test_classify_email_low_confidence_fallback():
    """Test that low confidence emails route to fallback"""
    mock_response = {
        "response": json.dumps({
            "predicted_folder": "Work",
            "secondary_labels": [],
            "confidence": 0.3,  # Below threshold
        })
    }
    # Should route to fallback_folder instead

# Test multiple fallback candidates
async def test_classify_email_fallback_selection():
    """Test fallback selection priority"""
    # Test with various folder lists
    # - MiscellaneousAndUncategorized priority
    # - Fallback to Miscellaneous, Uncategorized, INBOX
    # - Fallback to first available
```

**2. Folder Description Generation (Lines 153-182)**
- No error handling tests for LLM failures
- No tests for empty sample list
- No tests for partial/truncated responses

**Missing Test Cases:**
```python
async def test_generate_folder_description_empty_samples():
    """Test with no sample emails"""
    # Currently no error handling

async def test_generate_folder_description_partial_response():
    """Test with incomplete/truncated LLM response"""
    pass

async def test_generate_folder_description_network_failure():
    """Test httpx timeout or connection error"""
    pass
```

**3. Folder Structure Suggestion (Lines 184-237)**
- No tests for JSON parsing from array responses
- No tests for invalid/malformed array JSON
- No tests for empty suggestions fallback
- No error recovery tests

**Missing Test Cases:**
```python
async def test_suggest_folder_structure_malformed_array():
    """Test malformed JSON array response"""
    mock_response = {"response": "[invalid json"}
    # Currently no recovery, should fallback to INBOX

async def test_suggest_folder_structure_missing_fields():
    """Test array with missing required fields"""
    mock_response = {"response": json.dumps([
        {"name": "Work"},  # Missing description, criteria
        {"description": "No name field"},
    ])}
    pass

async def test_suggest_folder_structure_large_email_count():
    """Test with max_emails larger than available"""
    pass
```

**4. Folder Structure Refinement (Lines 239-352)**
- No tests for JSON repair mechanism (lines 300-311)
- No tests for category merging logic
- No tests for partial email assignments
- No tests for category preservation from existing list

**Missing Test Cases:**
```python
async def test_refine_folder_structure_json_repair():
    """Test JSON repair mechanism when initial parse fails"""
    mock_responses = [
        {"response": "{broken json"},  # Initial failure
        {"response": json.dumps({...})},  # Repair success
    ]
    pass

async def test_refine_folder_structure_partial_assignments():
    """Test when LLM doesn't assign all emails"""
    pass

async def test_refine_folder_structure_new_categories():
    """Test when LLM creates categories from assignments"""
    pass
```

**5. Category Normalization (Lines 373-455)**
- No tests for missing rename map entries
- No tests for repair_rename_map() failure scenarios
- No tests for consolidated category validation
- No tests for circular reference handling

**Missing Test Cases:**
```python
async def test_normalize_categories_incomplete_map():
    """Test repair when rename map is missing entries"""
    pass

async def test_normalize_categories_repair_failure():
    """Test graceful degradation when repair fails"""
    pass

async def test_normalize_categories_single_category():
    """Test with only one category (no consolidation)"""
    pass
```

**6. JSON Repair (Lines 354-371)**
- No tests for successful JSON repair
- No tests for both object and array repair attempts
- No tests for unrepairable JSON

**Missing Test Cases:**
```python
async def test_repair_json_object():
    """Test repair of malformed JSON object"""
    pass

async def test_repair_json_array():
    """Test repair of malformed JSON array"""
    pass

async def test_repair_json_unrepairable():
    """Test when JSON cannot be repaired"""
    pass
```

---

### 2. Main Module (0% coverage) - CRITICAL GAPS

**File:** `/home/john/src/mailmap/src/mailmap/main.py` (363 statements, all missing)

**This is the orchestration layer - NO TESTS AT ALL**

#### Critical Classes and Functions Missing Tests:

**1. EmailProcessor (Lines 24-79)**
- Queue management
- Email processing pipeline
- Error handling in processing loop

**Essential Tests:**
```python
async def test_email_processor_enqueue():
    """Test message queueing"""
    processor = EmailProcessor(config, db)
    msg = EmailMessage(...)
    processor.enqueue(msg)
    assert processor._queue.qsize() == 1

async def test_email_processor_processes_single_email():
    """Test full processing pipeline"""
    # Mock LLM and database
    # Verify email is classified and stored

async def test_email_processor_handles_classification_error():
    """Test error handling when classification fails"""
    # Should log error but not crash
    # Should continue processing next emails

async def test_email_processor_loop_handles_exceptions():
    """Test process_loop doesn't crash on email errors"""
    # Simulate email processing failure
    # Verify queue continues processing
```

**2. Database Sync (Lines 82-105)**
- IMAP folder synchronization
- New folder detection and insertion

**Essential Tests:**
```python
async def test_sync_folders_adds_new_folders():
    """Test new folders are added to database"""
    pass

async def test_sync_folders_skips_existing():
    """Test existing folders aren't duplicated"""
    pass

async def test_sync_folders_handles_imap_error():
    """Test graceful error handling on IMAP failures"""
    pass
```

**3. Folder Description Generation (Lines 108-153)**
- Sample email fetching
- LLM description generation
- Default description fallback

**Essential Tests:**
```python
async def test_generate_descriptions_fetches_samples():
    """Test email samples are fetched for each folder"""
    pass

async def test_generate_descriptions_skips_with_description():
    """Test folders with existing descriptions are skipped"""
    pass

async def test_generate_descriptions_default_fallback():
    """Test default description when no samples available"""
    pass

async def test_generate_descriptions_handles_fetch_error():
    """Test error handling for IMAP fetch failures"""
    pass
```

**4. Thunderbird Import (Lines 198-314)**
- Complex 3-phase import process
- Error handling at each phase
- Classification with batch processing

**Essential Tests:**
```python
async def test_import_from_thunderbird_phase1_sync():
    """Test Phase 1: Folder sync"""
    pass

async def test_import_from_thunderbird_phase2_descriptions():
    """Test Phase 2: Generate descriptions"""
    pass

async def test_import_from_thunderbird_phase3_classify():
    """Test Phase 3: Classify emails"""
    pass

async def test_import_from_thunderbird_skip_existing():
    """Test emails already imported are skipped"""
    pass

async def test_import_from_thunderbird_classification_failure():
    """Test continues on classification failure"""
    pass
```

**5. Folder Initialization (Lines 387-491)**
- Iterative batch processing
- Category normalization
- Database persistence

**Essential Tests:**
```python
async def test_init_folders_iterative_batching():
    """Test batching and refinement across multiple batches"""
    pass

async def test_init_folders_normalization():
    """Test category normalization after all batches"""
    pass

async def test_init_folders_rename_map_application():
    """Test rename map applied to assignments"""
    pass

async def test_init_folders_db_persistence():
    """Test folders created in database"""
    pass
```

**6. CLI Integration (Lines 499-663)**
- Configuration overrides
- Database reset
- All execution modes

**Essential Tests:**
```python
def test_apply_cli_overrides():
    """Test command-line config overrides"""
    pass

def test_reset_database():
    """Test database deletion"""
    pass

def test_main_list_mode():
    """Test --list classification results"""
    pass

def test_main_list_folders_mode():
    """Test --list-folders"""
    pass

def test_main_init_folders_mode():
    """Test --init-folders mode"""
    pass

def test_main_thunderbird_mode():
    """Test --thunderbird import"""
    pass
```

---

### 3. IMAP Client Module (29% coverage) - CRITICAL GAPS

**File:** `/home/john/src/mailmap/src/mailmap/imap_client.py` (167 statements, 118 missing)

#### Missing Test Coverage:

**1. ImapMailbox Class (Most methods untested)**
- `connect()` - Network connectivity
- `disconnect()` - Connection cleanup
- `list_folders()` - Folder enumeration
- `fetch_recent_uids()` - UID retrieval with filtering
- `fetch_email()` - Email retrieval and parsing
- `move_email()` - Email movement between folders

**2. ImapListener Class (Completely untested)**
- Async IDLE loop
- Email arrival callbacks
- Error recovery
- Connection state management

**3. Error Scenarios (Not tested)**
- Network timeouts
- Authentication failures
- Malformed email messages
- Connection drops during operation
- Concurrent access issues

**Essential Tests:**
```python
# Mock IMAP connection
async def test_imap_mailbox_connect():
    """Test IMAP connection establishment"""
    pass

async def test_imap_mailbox_list_folders():
    """Test folder enumeration"""
    pass

async def test_imap_mailbox_fetch_recent_uids():
    """Test UID retrieval with limit"""
    pass

async def test_imap_mailbox_fetch_email():
    """Test email retrieval and parsing"""
    pass

async def test_imap_listener_idle_loop():
    """Test IDLE monitoring for new emails"""
    pass

async def test_imap_listener_error_recovery():
    """Test reconnection after connection drop"""
    pass
```

---

### 4. MCP Server Module (0% coverage) - CRITICAL GAPS

**File:** `/home/john/src/mailmap/src/mailmap/mcp_server.py` (55 statements, all missing)

**MCP server interface completely untested - this provides the AI-facing API**

**Essential Tests:**
```python
async def test_mcp_server_initialization():
    """Test MCP server startup"""
    pass

async def test_mcp_server_tools_available():
    """Test MCP tools are properly registered"""
    pass

async def test_mcp_server_folder_list_tool():
    """Test list_folders tool"""
    pass

async def test_mcp_server_classify_tool():
    """Test classify_email tool"""
    pass

async def test_mcp_server_error_handling():
    """Test error responses"""
    pass
```

---

### 5. Thunderbird Module (72% coverage) - PARTIAL

**File:** `/home/john/src/mailmap/src/mailmap/thunderbird.py` (126 statements, 35 missing)

#### Missing Coverage (72% → 100%):

**Lines 31-67, 108-109, 130-131, 154, 166, 187, 189, 205, 207**

These appear to be error paths and edge cases:
- Profile detection edge cases
- File system errors
- Malformed mbox files
- Empty folder handling

**Tests to Add:**
```python
def test_thunderbird_reader_auto_detect_profile():
    """Test automatic profile detection when path is None"""
    # Currently tests explicit path only
    pass

def test_list_mbox_files_permission_error():
    """Test handling of permission denied errors"""
    pass

def test_read_mbox_corrupted_file():
    """Test handling of corrupted mbox files"""
    pass
```

---

## Test Failure Summary

**1 Test Failure - Simple Fix:**

**File:** `/home/john/src/mailmap/tests/test_config.py:43`
```python
def test_defaults(self):
    config = OllamaConfig()
    assert config.timeout_seconds == 120  # ❌ WRONG: default is 300
```

**Issue:** `OllamaConfig` has default `timeout_seconds=300` (line 23 in config.py), but test expects `120`.

**Fix:** Change test expectation to match actual default
```python
assert config.timeout_seconds == 300  # ✓ Correct default
```

---

## Deprecation Warnings

**18 warnings** about SQLite datetime adapters in Python 3.12+

**Issue:** SQLite built-in datetime serialization is deprecated

**Example Code to Fix:**
```python
# Current (deprecated)
def connect(self) -> None:
    self._conn = sqlite3.connect(self.path, check_same_thread=False)

# Should be changed to handle datetime properly with adapters
```

---

## Recommended Test Implementation Priority

### Phase 1: Critical (Unblock main functionality)
1. **main.py integration tests** (0% → 80%+)
   - EmailProcessor queue and processing
   - Thunderbird import phases
   - Folder initialization batching
   - Estimated effort: 16-20 tests, 200-250 lines

2. **llm.py error handling** (38% → 80%+)
   - JSON parsing failures and repair
   - Invalid folder fallback logic
   - Confidence threshold behavior
   - Estimated effort: 12-15 tests, 150-200 lines

3. **imap_client.py core functionality** (29% → 70%+)
   - Connection management
   - Folder operations
   - Email retrieval
   - Estimated effort: 10-12 tests, 150-200 lines

### Phase 2: Important (Improve reliability)
4. **mcp_server.py** (0% → 80%+)
   - Server initialization
   - Tool endpoint testing
   - Error responses
   - Estimated effort: 8-10 tests, 120-150 lines

5. **thunderbird.py** remaining gaps (72% → 95%+)
   - Edge cases and error handling
   - Estimated effort: 5-8 tests, 80-120 lines

### Phase 3: Polish (Reduce deprecation warnings)
6. **SQLite deprecation fixes**
   - Register datetime adapters
   - Estimated effort: 1-2 tests, 30-50 lines

---

## Test Coverage Roadmap

### Current State
- **Utility modules:** 100% (content.py, config.py, database.py)
- **Thunderbird:** 72%
- **LLM:** 38%
- **IMAP:** 29%
- **Main/Orchestration:** 0%
- **MCP Server:** 0%
- **Overall:** 36%

### Target State (Recommended)
- **Utility modules:** 100% (maintain)
- **All business logic:** 80%+
- **Integration paths:** 80%+
- **Error handling:** 85%+
- **Overall target:** 75%+

### Estimated Effort
- **Lines of test code:** 800-1000 new test cases
- **Number of tests:** 50-65 new test functions
- **Development time:** 2-3 weeks for one engineer
- **Maintenance cost:** Medium (LLM mocking complexity)

---

## Specific Test Examples

### Example 1: LLM Invalid Folder Handling
```python
@pytest.mark.asyncio
async def test_classify_email_invalid_folder_fallback(ollama_config):
    """Test classification with invalid folder routes to fallback"""
    mock_response = {
        "response": json.dumps({
            "predicted_folder": "InvalidFolder",  # Not in folder_descriptions
            "secondary_labels": [],
            "confidence": 0.95,
        })
    }

    async with OllamaClient(ollama_config) as client:
        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            result = await client.classify_email(
                subject="Test",
                from_addr="test@test.com",
                body="Test body",
                folder_descriptions={
                    "INBOX": "Main inbox",
                    "MiscellaneousAndUncategorized": "Misc",
                },
                confidence_threshold=0.5,
            )

            # Should fallback to MiscellaneousAndUncategorized
            assert result.predicted_folder == "MiscellaneousAndUncategorized"
            assert result.confidence == 0.0  # Zeroed due to invalid folder
```

### Example 2: EmailProcessor Error Handling
```python
@pytest.mark.asyncio
async def test_email_processor_handles_classification_error(sample_config, test_db):
    """Test processor continues on classification error"""
    processor = EmailProcessor(sample_config, test_db)

    # Mock LLM to fail
    with patch("mailmap.main.OllamaClient") as mock_llm_class:
        mock_llm = AsyncMock()
        mock_llm.classify_email.side_effect = RuntimeError("LLM error")
        mock_llm_class.return_value.__aenter__.return_value = mock_llm

        # Set up folder descriptions
        test_db.upsert_folder(Folder("INBOX", "Inbox", "Main inbox"))

        # Create message
        msg = EmailMessage(
            message_id="<test@test.com>",
            folder="INBOX",
            subject="Test",
            from_addr="sender@test.com",
            body_text="Test body",
        )

        # Should not raise, should log error
        await processor._process_email(msg)

        # Email should still be inserted
        retrieved = test_db.get_email("<test@test.com>")
        assert retrieved is not None
        assert retrieved.classification is None  # Not classified due to error
```

### Example 3: Thunderbird Import Three-Phase Test
```python
@pytest.mark.asyncio
async def test_import_from_thunderbird_complete_flow(sample_config, test_db):
    """Test complete three-phase import process"""

    # Phase 1: Sync folders
    # Mock ThunderbirdReader
    with patch("mailmap.main.ThunderbirdReader") as mock_reader_class:
        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.example.com"]
        mock_reader.list_folders.return_value = ["INBOX", "Sent"]

        # Mock samples for description generation
        mock_reader.get_sample_emails.return_value = [
            ThunderbirdEmail(
                message_id="<1@test>",
                folder="INBOX",
                subject="Test 1",
                from_addr="sender@test.com",
                body_text="Body 1",
            ),
        ]

        # Mock emails for import
        mock_reader.read_folder.return_value = [
            ThunderbirdEmail(
                message_id=f"<{i}@test>",
                folder="INBOX",
                subject=f"Subject {i}",
                from_addr="sender@test.com",
                body_text=f"Body {i}",
            )
            for i in range(5)
        ]

        mock_reader_class.return_value = mock_reader

        # Mock LLM
        with patch("mailmap.main.OllamaClient") as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm.generate_folder_description.return_value = FolderDescription(
                folder_id="INBOX",
                description="Main inbox"
            )
            mock_llm.classify_email.return_value = ClassificationResult(
                predicted_folder="INBOX",
                secondary_labels=[],
                confidence=0.9,
            )
            mock_llm_class.return_value.__aenter__.return_value = mock_llm

            # Run import
            await import_from_thunderbird(sample_config, test_db)

            # Verify all phases completed:
            # Phase 1: Folders synced
            assert test_db.get_folder("INBOX") is not None
            assert test_db.get_folder("Sent") is not None

            # Phase 2: Descriptions generated
            folder = test_db.get_folder("INBOX")
            assert folder.description == "Main inbox"

            # Phase 3: Emails imported and classified
            emails = test_db.get_all_emails()
            assert len(emails) == 5
            assert all(e.classification is not None for e in emails)
```

---

## Recommendations

### Immediate Actions (This Week)
1. ✅ Fix the 1 failing test in `test_config.py` (timeout_seconds default)
2. ✅ Add 5-8 critical LLM error handling tests
3. ✅ Add 8-10 EmailProcessor tests

### Short-term (Next 2 Weeks)
4. Add 12-15 main.py integration tests (Thunderbird import, CLI modes)
5. Add 10-12 IMAP client tests with mocking
6. Fix SQLite deprecation warnings

### Medium-term (Month 2)
7. Add 8-10 MCP server tests
8. Improve thunderbird.py to 95%+ coverage
9. Add integration tests across modules

### Long-term (Ongoing)
10. Establish CI/CD test gates (minimum 75% coverage)
11. Add property-based tests for JSON parsing robustness
12. Add performance/load tests for batch operations

---

## Testing Framework Recommendations

### Current Setup
- ✅ pytest (8.0.0+)
- ✅ pytest-asyncio (0.23.0+)
- ✅ unittest.mock (built-in)

### Recommended Additions
- **pytest-mock:** Simplifies mock/patch usage
- **pytest-timeout:** Prevents hanging tests
- **faker:** Generate realistic test data (emails, names, etc.)
- **hypothesis:** Property-based testing for JSON parsing

### Mock Strategy
- **IMAP:** Mock `imapclient.IMAPClient` connections
- **LLM:** Mock `httpx.AsyncClient.post()` responses
- **Database:** Use temporary SQLite files (already in place)
- **File I/O:** Mock file system for Thunderbird reader tests

---

## Conclusion

The mailmap project has **excellent foundational test coverage** for utility and data layer modules (100% for config, content, database), but **critical gaps in the integration and orchestration layers** where bugs are most likely to occur.

**Highest priority:** Add tests for `main.py` (0% → 80%+) to cover the three-phase import, batch processing, and error handling workflows.

**Secondary priority:** Strengthen `llm.py` error handling and JSON parsing resilience.

**Timeline:** 50-65 new tests (800-1000 lines) to reach 75% overall coverage over 3-4 weeks.

