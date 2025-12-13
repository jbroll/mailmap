# Recommended Test Cases - Implementation Guide

This document provides concrete, ready-to-implement test cases for the critical gaps identified in the coverage analysis.

---

## 1. LLM Module Tests - Error Handling & JSON Parsing

### File: `tests/test_llm_advanced.py`

```python
"""Advanced tests for LLM response parsing and error handling."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx
from mailmap.config import OllamaConfig
from mailmap.llm import OllamaClient, SuggestedFolder


@pytest.fixture
def ollama_config():
    return OllamaConfig(
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )


class TestClassifyEmailErrorHandling:
    """Test error cases and fallback logic in email classification."""

    @pytest.mark.asyncio
    async def test_classify_invalid_folder_fallback(self, ollama_config):
        """Test that invalid LLM folder response triggers fallback to valid folder."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "InvalidFolderName",  # Not in descriptions
                "secondary_labels": ["Tag1"],
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
                    subject="Test email",
                    from_addr="test@example.com",
                    body="Test body",
                    folder_descriptions={
                        "INBOX": "Main inbox",
                        "Work": "Work emails",
                        "MiscellaneousAndUncategorized": "Uncategorized",
                    },
                    confidence_threshold=0.5,
                )

                # Should use fallback folder, not invalid folder
                assert result.predicted_folder == "MiscellaneousAndUncategorized"
                assert result.confidence == 0.0  # Reset for invalid folder

    @pytest.mark.asyncio
    async def test_classify_low_confidence_fallback(self, ollama_config):
        """Test that low confidence results route to fallback folder."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                "confidence": 0.3,  # Below default 0.5 threshold
            })
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.classify_email(
                    subject="Ambiguous",
                    from_addr="test@example.com",
                    body="Could be anything",
                    folder_descriptions={"INBOX": "Main", "Work": "Work"},
                    confidence_threshold=0.5,
                    fallback_folder="INBOX",
                )

                # Should fallback to INBOX
                assert result.predicted_folder == "INBOX"

    @pytest.mark.asyncio
    async def test_classify_no_fallback_candidates_uses_first(self, ollama_config):
        """Test fallback selection when no standard candidates exist."""
        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": "invalid"}
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                # No standard fallback folders
                folder_descriptions = {
                    "ProjectA": "First project",
                    "ProjectB": "Second project",
                }

                result = await client.classify_email(
                    subject="Test",
                    from_addr="test@test.com",
                    body="Body",
                    folder_descriptions=folder_descriptions,
                    fallback_folder=None,  # Not specified
                )

                # Should fallback to first folder
                assert result.predicted_folder in folder_descriptions

    @pytest.mark.asyncio
    async def test_classify_empty_folder_descriptions(self, ollama_config):
        """Test behavior with no folders available."""
        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": json.dumps({})}
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.classify_email(
                    subject="Test",
                    from_addr="test@test.com",
                    body="Body",
                    folder_descriptions={},  # Empty
                    fallback_folder=None,
                )

                # Should default to None fallback
                assert result.predicted_folder is None or result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_negative_confidence_handling(self, ollama_config):
        """Test handling of negative confidence values."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                "confidence": -0.5,  # Nonsensical negative value
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
                    body="Body",
                    folder_descriptions={"INBOX": "Main", "Work": "Work"},
                    confidence_threshold=0.5,
                )

                # Negative confidence should trigger fallback
                assert result.predicted_folder == "INBOX"  # Fallback

    @pytest.mark.asyncio
    async def test_classify_missing_confidence_field(self, ollama_config):
        """Test handling when confidence field is missing from response."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                # Missing "confidence" field
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
                    body="Body",
                    folder_descriptions={"INBOX": "Main", "Work": "Work"},
                )

                # Should default to 0.0 confidence and use fallback
                assert result.confidence == 0.0
                assert result.predicted_folder == "INBOX"


class TestGenerateFolderDescriptionErrorCases:
    """Test error handling in folder description generation."""

    @pytest.mark.asyncio
    async def test_generate_description_empty_samples(self, ollama_config):
        """Test folder description generation with no sample emails."""
        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": "Description from empty"}
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.generate_folder_description(
                    folder_name="Work",
                    sample_emails=[],  # No samples
                )

                # Should still return valid result
                assert result.folder_id == "Work"
                assert isinstance(result.description, str)
                assert len(result.description) > 0

    @pytest.mark.asyncio
    async def test_generate_description_very_long_response(self, ollama_config):
        """Test handling of very long description responses."""
        long_description = "A" * 5000  # Very long description

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"response": long_description}
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.generate_folder_description(
                    folder_name="Work",
                    sample_emails=[{"subject": "Test", "from_addr": "test@test.com", "body": "Body"}],
                )

                # Should accept long descriptions
                assert result.description == long_description


class TestSuggestFolderStructureErrorCases:
    """Test error handling in folder structure suggestion."""

    @pytest.mark.asyncio
    async def test_suggest_structure_malformed_array(self, ollama_config):
        """Test recovery when LLM returns malformed JSON array."""
        mock_response = {
            "response": '[{"name": "Work", "description": "Work emails"'  # Incomplete
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.suggest_folder_structure(
                    sample_emails=[
                        {"subject": "Work email", "from_addr": "coworker@work.com", "body": "Meeting details"},
                        {"subject": "Personal", "from_addr": "friend@example.com", "body": "Catching up"},
                    ],
                )

                # Should fallback to INBOX
                assert len(result) == 1
                assert result[0].name == "INBOX"

    @pytest.mark.asyncio
    async def test_suggest_structure_missing_fields(self, ollama_config):
        """Test handling of incomplete folder definitions in response."""
        mock_response = {
            "response": json.dumps([
                {"name": "Work"},  # Missing description
                {"description": "No name"},  # Missing name
                {"name": "Personal", "description": "Personal emails"},  # Complete
            ])
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.suggest_folder_structure(
                    sample_emails=[
                        {"subject": "Test", "from_addr": "test@test.com", "body": "Body"},
                    ],
                )

                # Should include personal but handle incomplete entries
                personal = [f for f in result if f.name == "Personal"]
                assert len(personal) > 0

    @pytest.mark.asyncio
    async def test_suggest_structure_empty_array(self, ollama_config):
        """Test handling of empty array response."""
        mock_response = {"response": "[]"}

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.suggest_folder_structure(
                    sample_emails=[
                        {"subject": "Test", "from_addr": "test@test.com", "body": "Body"},
                    ],
                )

                # Should fallback to INBOX
                assert len(result) >= 1
                assert result[0].name == "INBOX"


class TestJSONRepair:
    """Test JSON repair mechanism."""

    @pytest.mark.asyncio
    async def test_repair_json_missing_closing_brace(self, ollama_config):
        """Test repair of JSON missing closing braces."""
        # First call returns broken JSON, second returns repair
        responses = [
            {"response": "{incomplete"},
            {"response": '{"repaired": true}'},
        ]

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.raise_for_status = MagicMock()
                mock_post.side_effect = [
                    MagicMock(json=lambda: responses[0], raise_for_status=MagicMock()),
                    MagicMock(json=lambda: responses[1], raise_for_status=MagicMock()),
                ]

                repaired = await client.repair_json("{incomplete")

                # Should attempt repair
                assert repaired is not None


class TestNormalizeCategoriesErrorCases:
    """Test error handling in category normalization."""

    @pytest.mark.asyncio
    async def test_normalize_single_category_no_consolidation(self, ollama_config):
        """Test that single category doesn't need consolidation."""
        categories = [
            SuggestedFolder(
                name="General",
                description="General emails",
                example_criteria=["Everything"],
            )
        ]

        async with OllamaClient(ollama_config) as client:
            # Should short-circuit without LLM call
            result, rename_map = await client.normalize_categories(categories)

            assert len(result) == 1
            assert result[0].name == "General"
            assert rename_map == {"General": "General"}

    @pytest.mark.asyncio
    async def test_normalize_empty_category_list(self, ollama_config):
        """Test normalization of empty category list."""
        categories = []

        async with OllamaClient(ollama_config) as client:
            result, rename_map = await client.normalize_categories(categories)

            assert len(result) == 0
            assert len(rename_map) == 0

    @pytest.mark.asyncio
    async def test_normalize_incomplete_rename_map_repair(self, ollama_config):
        """Test repair of incomplete rename maps."""
        mock_responses = [
            # First call returns incomplete map
            {
                "response": json.dumps({
                    "consolidated_categories": [
                        {"name": "Work", "description": "Work emails", "merged_from": ["ProjectA", "ProjectB"]},
                    ],
                    "rename_map": {"ProjectA": "Work"},  # Missing ProjectB
                })
            },
            # Second call repairs the missing mapping
            {
                "response": json.dumps({
                    "mappings": {"ProjectB": "Work"},
                })
            },
        ]

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp1 = MagicMock()
                mock_resp1.json.return_value = mock_responses[0]
                mock_resp1.raise_for_status = MagicMock()

                mock_resp2 = MagicMock()
                mock_resp2.json.return_value = mock_responses[1]
                mock_resp2.raise_for_status = MagicMock()

                mock_post.side_effect = [mock_resp1, mock_resp2]

                categories = [
                    SuggestedFolder("ProjectA", "Project A work", []),
                    SuggestedFolder("ProjectB", "Project B work", []),
                    SuggestedFolder("Personal", "Personal emails", []),
                ]

                result, rename_map = await client.normalize_categories(categories)

                # All original categories should be in rename_map
                assert "ProjectA" in rename_map
                assert "ProjectB" in rename_map
```

---

## 2. Main Module Tests - Integration & Orchestration

### File: `tests/test_main_email_processor.py`

```python
"""Tests for EmailProcessor and email processing pipeline."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from mailmap.config import Config, ImapConfig, OllamaConfig, DatabaseConfig
from mailmap.database import Database, Folder, Email
from mailmap.imap_client import EmailMessage
from mailmap.llm import ClassificationResult
from mailmap.main import EmailProcessor


@pytest.fixture
def email_processor_setup(temp_dir):
    """Set up email processor with test database."""
    db = Database(temp_dir / "test.db")
    db.connect()
    db.init_schema()

    config = Config(
        imap=ImapConfig(host="test.com", username="user", password="pass"),
        ollama=OllamaConfig(),
        database=DatabaseConfig(path=str(temp_dir / "test.db")),
    )

    # Add a test folder
    db.upsert_folder(Folder("INBOX", "Inbox", "Main inbox"))
    db.upsert_folder(Folder("Work", "Work", "Work emails"))

    yield config, db

    db.close()


class TestEmailProcessor:
    """Test email processing pipeline."""

    @pytest.mark.asyncio
    async def test_enqueue_message(self, email_processor_setup):
        """Test adding message to processing queue."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        msg = EmailMessage(
            message_id="<test1@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="Test body",
        )

        processor.enqueue(msg)

        assert processor._queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_process_email_stores_in_database(self, email_processor_setup):
        """Test that processed email is stored in database."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        msg = EmailMessage(
            message_id="<test@example.com>",
            folder="INBOX",
            subject="Test Subject",
            from_addr="sender@example.com",
            body_text="Test body",
        )

        with patch("mailmap.main.OllamaClient") as mock_llm:
            mock_llm.return_value.__aenter__.return_value.classify_email.return_value = (
                ClassificationResult("Work", [], 0.95)
            )

            await processor._process_email(msg)

        # Verify email stored
        stored = db.get_email("<test@example.com>")
        assert stored is not None
        assert stored.subject == "Test Subject"

    @pytest.mark.asyncio
    async def test_process_email_classification(self, email_processor_setup):
        """Test email classification and confidence storage."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        msg = EmailMessage(
            message_id="<class@example.com>",
            folder="INBOX",
            subject="Work discussion",
            from_addr="boss@company.com",
            body_text="Project details",
        )

        with patch("mailmap.main.OllamaClient") as mock_llm:
            mock_llm.return_value.__aenter__.return_value.classify_email.return_value = (
                ClassificationResult("Work", ["Important"], 0.92)
            )

            await processor._process_email(msg)

        # Verify classification stored
        stored = db.get_email("<class@example.com>")
        assert stored.classification == "Work"
        assert stored.confidence == 0.92

    @pytest.mark.asyncio
    async def test_process_email_no_folder_descriptions(self, email_processor_setup):
        """Test handling when no folder descriptions available."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        # Remove folder descriptions
        db.conn.execute("UPDATE folders SET description = NULL")
        db.conn.commit()

        msg = EmailMessage(
            message_id="<nodesc@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="test@example.com",
            body_text="Body",
        )

        # Should not raise, just skip classification
        await processor._process_email(msg)

        stored = db.get_email("<nodesc@example.com>")
        assert stored.classification is None

    @pytest.mark.asyncio
    async def test_process_email_handles_llm_error(self, email_processor_setup):
        """Test that classification errors don't crash processing."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        msg = EmailMessage(
            message_id="<error@example.com>",
            folder="INBOX",
            subject="Test",
            from_addr="test@example.com",
            body_text="Body",
        )

        with patch("mailmap.main.OllamaClient") as mock_llm:
            mock_llm.return_value.__aenter__.return_value.classify_email.side_effect = (
                RuntimeError("LLM connection failed")
            )

            # Should not raise
            await processor._process_email(msg)

        # Email still stored
        stored = db.get_email("<error@example.com>")
        assert stored is not None
        assert stored.classification is None

    @pytest.mark.asyncio
    async def test_process_loop_continues_on_error(self, email_processor_setup):
        """Test that process loop continues processing after error."""
        config, db = email_processor_setup
        processor = EmailProcessor(config, db)

        msg1 = EmailMessage(
            message_id="<msg1@example.com>",
            folder="INBOX",
            subject="Good",
            from_addr="test@example.com",
            body_text="Body",
        )

        msg2 = EmailMessage(
            message_id="<msg2@example.com>",
            folder="INBOX",
            subject="Will fail",
            from_addr="test@example.com",
            body_text="Body",
        )

        msg3 = EmailMessage(
            message_id="<msg3@example.com>",
            folder="INBOX",
            subject="Good again",
            from_addr="test@example.com",
            body_text="Body",
        )

        processor.enqueue(msg1)
        processor.enqueue(msg2)
        processor.enqueue(msg3)

        process_task = asyncio.create_task(processor.process_loop())

        with patch("mailmap.main.OllamaClient") as mock_llm:
            # First call succeeds
            # Second call fails
            # Third call succeeds
            success = ClassificationResult("INBOX", [], 0.8)
            mock_llm.return_value.__aenter__.return_value.classify_email.side_effect = [
                success,
                RuntimeError("LLM failed"),
                success,
            ]

            # Give processor time to process
            await asyncio.sleep(0.1)

            # Verify all 3 emails were processed despite middle error
            assert db.get_email("<msg1@example.com>") is not None
            assert db.get_email("<msg2@example.com>") is not None
            assert db.get_email("<msg3@example.com>") is not None

        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
```

---

## 3. Main Module Tests - Three-Phase Import

### File: `tests/test_main_thunderbird_import.py`

```python
"""Tests for Thunderbird import integration."""

from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from mailmap.config import Config, ImapConfig, OllamaConfig, DatabaseConfig, ThunderbirdConfig
from mailmap.database import Database, Folder, Email
from mailmap.thunderbird import ThunderbirdEmail
from mailmap.llm import ClassificationResult, FolderDescription
from mailmap.main import import_from_thunderbird


@pytest.fixture
def thunderbird_import_setup(temp_dir):
    """Set up database for import testing."""
    db = Database(temp_dir / "test.db")
    db.connect()
    db.init_schema()

    config = Config(
        imap=ImapConfig(host="test.com", username="user", password="pass"),
        ollama=OllamaConfig(),
        database=DatabaseConfig(path=str(temp_dir / "test.db")),
        thunderbird=ThunderbirdConfig(profile_path="/fake/profile"),
    )

    yield config, db

    db.close()


class TestThunderbirdImportPhases:
    """Test three-phase import process."""

    @pytest.mark.asyncio
    async def test_import_phase1_folder_sync(self, thunderbird_import_setup):
        """Test Phase 1: Folders are synced from Thunderbird."""
        config, db = thunderbird_import_setup

        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.example.com"]
        mock_reader.list_folders.return_value = ["INBOX", "Sent", "Drafts", "Work"]
        mock_reader.get_sample_emails.return_value = []
        mock_reader.read_folder.return_value = iter([])

        with patch("mailmap.main.ThunderbirdReader", return_value=mock_reader):
            with patch("mailmap.main.OllamaClient"):
                await import_from_thunderbird(config, db)

        # Verify folders created
        inbox = db.get_folder("INBOX")
        assert inbox is not None
        assert inbox.folder_id == "INBOX"

        work = db.get_folder("Work")
        assert work is not None

    @pytest.mark.asyncio
    async def test_import_phase2_description_generation(self, thunderbird_import_setup):
        """Test Phase 2: Folder descriptions are generated."""
        config, db = thunderbird_import_setup

        sample_email = ThunderbirdEmail(
            message_id="<sample@test.com>",
            folder="INBOX",
            subject="Sample email",
            from_addr="sender@test.com",
            body_text="Sample body",
        )

        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.test.com"]
        mock_reader.list_folders.return_value = ["INBOX"]
        mock_reader.get_sample_emails.return_value = [sample_email]
        mock_reader.read_folder.return_value = iter([])

        with patch("mailmap.main.ThunderbirdReader", return_value=mock_reader):
            mock_llm = AsyncMock()
            mock_llm.generate_folder_description.return_value = FolderDescription(
                folder_id="INBOX",
                description="Main inbox containing all incoming emails",
            )

            with patch("mailmap.main.OllamaClient") as mock_llm_class:
                mock_llm_class.return_value.__aenter__.return_value = mock_llm

                await import_from_thunderbird(config, db)

        # Verify description set
        folder = db.get_folder("INBOX")
        assert folder.description == "Main inbox containing all incoming emails"

    @pytest.mark.asyncio
    async def test_import_phase3_email_classification(self, thunderbird_import_setup):
        """Test Phase 3: Emails are imported and classified."""
        config, db = thunderbird_import_setup

        # Create test emails
        emails_to_import = [
            ThunderbirdEmail(
                message_id=f"<email{i}@test.com>",
                folder="INBOX",
                subject=f"Email {i}",
                from_addr=f"sender{i}@test.com",
                body_text=f"Body {i}",
            )
            for i in range(5)
        ]

        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.test.com"]
        mock_reader.list_folders.return_value = ["INBOX"]
        mock_reader.get_sample_emails.return_value = []
        mock_reader.read_folder.return_value = iter(emails_to_import)

        with patch("mailmap.main.ThunderbirdReader", return_value=mock_reader):
            mock_llm = AsyncMock()
            mock_llm.generate_folder_description.return_value = FolderDescription(
                folder_id="INBOX",
                description="Main inbox",
            )
            mock_llm.classify_email.return_value = ClassificationResult(
                predicted_folder="INBOX",
                secondary_labels=[],
                confidence=0.9,
            )

            with patch("mailmap.main.OllamaClient") as mock_llm_class:
                mock_llm_class.return_value.__aenter__.return_value = mock_llm

                await import_from_thunderbird(config, db)

        # Verify emails imported
        for i in range(5):
            email = db.get_email(f"<email{i}@test.com>")
            assert email is not None
            assert email.subject == f"Email {i}"
            assert email.classification == "INBOX"

    @pytest.mark.asyncio
    async def test_import_skips_existing_emails(self, thunderbird_import_setup):
        """Test that emails already imported are skipped."""
        config, db = thunderbird_import_setup

        # Pre-insert an email
        existing_email = Email(
            message_id="<existing@test.com>",
            folder_id="INBOX",
            subject="Already imported",
            from_addr="test@test.com",
            body_text="Body",
            processed_at=datetime.now(),
        )
        db.upsert_folder(Folder("INBOX", "Inbox"))
        db.insert_email(existing_email)

        # Try to import same email
        email_to_import = ThunderbirdEmail(
            message_id="<existing@test.com>",
            folder="INBOX",
            subject="Should not update",
            from_addr="test@test.com",
            body_text="New body",
        )

        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.test.com"]
        mock_reader.list_folders.return_value = ["INBOX"]
        mock_reader.get_sample_emails.return_value = []
        mock_reader.read_folder.return_value = iter([email_to_import])

        with patch("mailmap.main.ThunderbirdReader", return_value=mock_reader):
            mock_llm = AsyncMock()
            mock_llm.generate_folder_description.return_value = FolderDescription(
                folder_id="INBOX",
                description="Main inbox",
            )

            with patch("mailmap.main.OllamaClient") as mock_llm_class:
                mock_llm_class.return_value.__aenter__.return_value = mock_llm

                await import_from_thunderbird(config, db)

        # Verify original email unchanged
        stored = db.get_email("<existing@test.com>")
        assert stored.subject == "Already imported"
        assert stored.body_text == "Body"  # Not updated

    @pytest.mark.asyncio
    async def test_import_continues_on_classification_error(self, thunderbird_import_setup):
        """Test that classification errors don't stop import."""
        config, db = thunderbird_import_setup

        emails_to_import = [
            ThunderbirdEmail(
                message_id="<good1@test.com>",
                folder="INBOX",
                subject="Good",
                from_addr="test@test.com",
                body_text="Body",
            ),
            ThunderbirdEmail(
                message_id="<bad@test.com>",
                folder="INBOX",
                subject="Will fail",
                from_addr="test@test.com",
                body_text="Body",
            ),
            ThunderbirdEmail(
                message_id="<good2@test.com>",
                folder="INBOX",
                subject="Good again",
                from_addr="test@test.com",
                body_text="Body",
            ),
        ]

        mock_reader = MagicMock()
        mock_reader.profile_path = Path("/fake/profile")
        mock_reader.list_servers.return_value = ["imap.test.com"]
        mock_reader.list_folders.return_value = ["INBOX"]
        mock_reader.get_sample_emails.return_value = []
        mock_reader.read_folder.return_value = iter(emails_to_import)

        with patch("mailmap.main.ThunderbirdReader", return_value=mock_reader):
            mock_llm = AsyncMock()
            mock_llm.generate_folder_description.return_value = FolderDescription(
                folder_id="INBOX",
                description="Main inbox",
            )
            # First succeeds, second fails, third succeeds
            mock_llm.classify_email.side_effect = [
                ClassificationResult("INBOX", [], 0.9),
                RuntimeError("LLM failed"),
                ClassificationResult("INBOX", [], 0.9),
            ]

            with patch("mailmap.main.OllamaClient") as mock_llm_class:
                mock_llm_class.return_value.__aenter__.return_value = mock_llm

                # Should not raise
                await import_from_thunderbird(config, db)

        # Verify all emails imported despite middle failure
        assert db.get_email("<good1@test.com>") is not None
        assert db.get_email("<bad@test.com>") is not None  # Still imported
        assert db.get_email("<good2@test.com>") is not None

        # Verify first and third classified
        assert db.get_email("<good1@test.com>").classification == "INBOX"
        assert db.get_email("<good2@test.com>").classification == "INBOX"

        # Verify second not classified
        assert db.get_email("<bad@test.com>").classification is None
```

---

## 4. Quick Test Run Instructions

### Run only new LLM tests:
```bash
python -m pytest tests/test_llm_advanced.py -v
```

### Run only new main/integration tests:
```bash
python -m pytest tests/test_main_email_processor.py tests/test_main_thunderbird_import.py -v
```

### Run all tests with coverage:
```bash
python -m pytest tests/ --cov=mailmap --cov-report=html
```

### Run specific test:
```bash
python -m pytest tests/test_llm_advanced.py::TestClassifyEmailErrorHandling::test_classify_invalid_folder_fallback -v
```

---

## Implementation Priority

1. **Week 1:** LLM error handling tests (15 tests, ~300 lines)
2. **Week 2:** EmailProcessor tests (10 tests, ~250 lines)
3. **Week 3:** Thunderbird import tests (8 tests, ~200 lines)
4. **Week 4:** IMAP client tests and polish

This prioritization targets the highest-risk code paths first while being implementable by a single engineer.

