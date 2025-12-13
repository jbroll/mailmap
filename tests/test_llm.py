"""Tests for LLM module."""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from mailmap.config import OllamaConfig
from mailmap.llm import (
    OllamaClient,
    ClassificationResult,
    FolderDescription,
    SuggestedFolder,
    load_prompt,
    _format_email_samples,
)


@pytest.fixture
def ollama_config():
    return OllamaConfig(
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )


class TestLoadPrompt:
    """Tests for load_prompt function with path traversal protection."""

    def test_load_valid_prompt(self):
        """Should load a valid prompt template."""
        prompt = load_prompt("classify_email")
        assert "email" in prompt.lower()
        assert "{" in prompt  # Should have template variables

    def test_path_traversal_slash(self):
        """Should reject names with forward slashes."""
        with pytest.raises(ValueError, match="Invalid prompt name"):
            load_prompt("../etc/passwd")

    def test_path_traversal_backslash(self):
        """Should reject names with backslashes."""
        with pytest.raises(ValueError, match="Invalid prompt name"):
            load_prompt("..\\etc\\passwd")

    def test_path_traversal_dotdot(self):
        """Should reject names with double dots."""
        with pytest.raises(ValueError, match="Invalid prompt name"):
            load_prompt("..passwd")

    def test_nonexistent_prompt(self):
        """Should raise FileNotFoundError for missing prompts."""
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_prompt_xyz")

    def test_prompt_caching(self):
        """Should cache prompt templates."""
        # Clear cache first
        load_prompt.cache_clear()

        # Load twice
        prompt1 = load_prompt("classify_email")
        prompt2 = load_prompt("classify_email")

        # Should be same object (cached)
        assert prompt1 is prompt2

        # Check cache stats
        info = load_prompt.cache_info()
        assert info.hits >= 1


class TestFormatEmailSamples:
    """Tests for _format_email_samples helper."""

    def test_format_single_email(self):
        """Should format a single email correctly."""
        emails = [{"subject": "Test", "from_addr": "test@test.com", "body": "Hello"}]
        result = _format_email_samples(emails, max_emails=1)
        assert "Email 1:" in result
        assert "Test" in result
        assert "test@test.com" in result

    def test_format_respects_max_emails(self):
        """Should limit to max_emails."""
        emails = [
            {"subject": f"Email {i}", "from_addr": f"user{i}@test.com", "body": "Body"}
            for i in range(10)
        ]
        result = _format_email_samples(emails, max_emails=3)
        assert "Email 1:" in result
        assert "Email 3:" in result
        assert "Email 4:" not in result

    def test_format_handles_missing_fields(self):
        """Should handle missing email fields gracefully."""
        emails = [{}]  # Empty dict
        result = _format_email_samples(emails, max_emails=1)
        assert "no subject" in result
        assert "unknown" in result


class TestClassificationResult:
    def test_dataclass(self):
        result = ClassificationResult(
            predicted_folder="INBOX",
            secondary_labels=["Work", "Important"],
            confidence=0.95,
        )
        assert result.predicted_folder == "INBOX"
        assert result.secondary_labels == ["Work", "Important"]
        assert result.confidence == 0.95


class TestFolderDescription:
    def test_dataclass(self):
        desc = FolderDescription(
            folder_id="Receipts",
            description="Contains purchase receipts and order confirmations",
        )
        assert desc.folder_id == "Receipts"
        assert "receipts" in desc.description.lower()


class TestSuggestedFolder:
    def test_dataclass(self):
        folder = SuggestedFolder(
            name="Finance",
            description="Financial emails",
            example_criteria=["invoices", "statements"],
        )
        assert folder.name == "Finance"
        assert folder.description == "Financial emails"
        assert len(folder.example_criteria) == 2


class TestOllamaClient:
    @pytest.mark.asyncio
    async def test_context_manager(self, ollama_config):
        async with OllamaClient(ollama_config) as client:
            assert client._client is not None
        assert client._client is None

    @pytest.mark.asyncio
    async def test_client_not_initialized_error(self, ollama_config):
        client = OllamaClient(ollama_config)
        with pytest.raises(RuntimeError, match="Client not initialized"):
            _ = client.client


class TestOllamaClientJsonExtraction:
    """Tests for JSON extraction and parsing methods."""

    @pytest.mark.asyncio
    async def test_extract_json_object(self, ollama_config):
        """Should extract JSON object from text."""
        async with OllamaClient(ollama_config) as client:
            text = 'Some text {"key": "value"} more text'
            result = client._extract_json(text)
            assert result == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_extract_json_array(self, ollama_config):
        """Should extract JSON array from text."""
        async with OllamaClient(ollama_config) as client:
            text = 'Array: [1, 2, 3] done'
            result = client._extract_json(text, '[', ']')
            assert result == '[1, 2, 3]'

    @pytest.mark.asyncio
    async def test_extract_json_not_found(self, ollama_config):
        """Should return None when no JSON found."""
        async with OllamaClient(ollama_config) as client:
            result = client._extract_json("no json here")
            assert result is None

    @pytest.mark.asyncio
    async def test_parse_json_success(self, ollama_config):
        """Should parse valid JSON from text."""
        async with OllamaClient(ollama_config) as client:
            text = '{"key": "value"}'
            result = client._parse_json(text)
            assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_parse_json_invalid(self, ollama_config):
        """Should return None for invalid JSON."""
        async with OllamaClient(ollama_config) as client:
            text = "{invalid json}"
            result = client._parse_json(text)
            assert result is None


class TestClassifyEmail:
    """Tests for email classification."""

    @pytest.mark.asyncio
    async def test_classify_email_success(self, ollama_config):
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Receipts",
                "secondary_labels": ["Shopping", "Finance"],
                "confidence": 0.92,
            })
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.classify_email(
                    subject="Your Amazon order has shipped",
                    from_addr="ship-confirm@amazon.com",
                    body="Your order #123 has shipped...",
                    folder_descriptions={
                        "INBOX": "General inbox",
                        "Receipts": "Purchase receipts",
                    },
                )

                assert result.predicted_folder == "Receipts"
                assert result.secondary_labels == ["Shopping", "Finance"]
                assert result.confidence == 0.92

    @pytest.mark.asyncio
    async def test_classify_email_malformed_json(self, ollama_config):
        """Should return fallback on malformed JSON."""
        mock_response = {"response": "This is not valid JSON at all"}

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
                    folder_descriptions={"INBOX": "Inbox"},
                )

                assert result.predicted_folder == "INBOX"
                assert result.secondary_labels == []
                assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_email_extracts_json_from_text(self, ollama_config):
        """Should extract JSON embedded in text."""
        mock_response = {
            "response": 'Based on analysis:\n{"predicted_folder": "Work", "secondary_labels": [], "confidence": 0.85}'
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.classify_email(
                    subject="Meeting tomorrow",
                    from_addr="boss@company.com",
                    body="Let's meet tomorrow",
                    folder_descriptions={"Work": "Work emails"},
                )

                assert result.predicted_folder == "Work"
                assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_classify_email_invalid_folder_uses_fallback(self, ollama_config):
        """Should use fallback when LLM returns invalid folder."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "NonexistentFolder",
                "secondary_labels": [],
                "confidence": 0.9,
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
                        "INBOX": "General inbox",
                        "Work": "Work emails",
                    },
                )

                # Should fall back to INBOX (first fallback candidate)
                assert result.predicted_folder == "INBOX"
                assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_email_low_confidence_uses_fallback(self, ollama_config):
        """Should use fallback when confidence is below threshold."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                "confidence": 0.3,  # Below default threshold of 0.5
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
                        "Miscellaneous": "Catch-all folder",
                        "Work": "Work emails",
                    },
                )

                # Should use Miscellaneous as fallback
                assert result.predicted_folder == "Miscellaneous"
                assert result.confidence == 0.3  # Original confidence preserved

    @pytest.mark.asyncio
    async def test_classify_email_custom_threshold(self, ollama_config):
        """Should respect custom confidence threshold."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                "confidence": 0.7,
            })
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                # With threshold 0.8, confidence 0.7 should use fallback
                result = await client.classify_email(
                    subject="Test",
                    from_addr="test@test.com",
                    body="Test body",
                    folder_descriptions={
                        "INBOX": "General inbox",
                        "Work": "Work emails",
                    },
                    confidence_threshold=0.8,
                )

                assert result.predicted_folder == "INBOX"

    @pytest.mark.asyncio
    async def test_classify_email_finds_miscellaneous_fallback(self, ollama_config):
        """Should find MiscellaneousAndUncategorized as fallback."""
        mock_response = {"response": "invalid"}

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
                        "Work": "Work emails",
                        "MiscellaneousAndUncategorized": "Catch-all",
                    },
                )

                assert result.predicted_folder == "MiscellaneousAndUncategorized"

    @pytest.mark.asyncio
    async def test_classify_email_invalid_confidence_type(self, ollama_config):
        """Should handle invalid confidence type gracefully."""
        mock_response = {
            "response": json.dumps({
                "predicted_folder": "Work",
                "secondary_labels": [],
                "confidence": "high",  # Invalid - should be float
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
                    folder_descriptions={"Work": "Work emails"},
                )

                # Should default to 0.0 confidence
                assert result.confidence == 0.0


class TestGenerateFolderDescription:
    """Tests for folder description generation."""

    @pytest.mark.asyncio
    async def test_generate_folder_description(self, ollama_config):
        mock_response = {
            "response": "Contains order confirmations and purchase receipts."
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.generate_folder_description(
                    folder_name="Receipts",
                    sample_emails=[
                        {"subject": "Order confirmed", "from_addr": "amazon@amazon.com", "body": "Your order..."},
                    ],
                )

                assert result.folder_id == "Receipts"
                assert "order" in result.description.lower()


class TestSuggestFolderStructure:
    """Tests for folder structure suggestion."""

    @pytest.mark.asyncio
    async def test_suggest_folder_structure_success(self, ollama_config):
        mock_response = {
            "response": json.dumps([
                {"name": "Finance", "description": "Financial emails", "example_criteria": ["invoices"]},
                {"name": "Work", "description": "Work emails", "example_criteria": ["meetings"]},
            ])
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.suggest_folder_structure(
                    sample_emails=[{"subject": "Invoice", "from_addr": "billing@example.com", "body": "..."}],
                )

                assert len(result) == 2
                assert result[0].name == "Finance"
                assert result[1].name == "Work"

    @pytest.mark.asyncio
    async def test_suggest_folder_structure_fallback(self, ollama_config):
        """Should return INBOX fallback on parse failure."""
        mock_response = {"response": "invalid json"}

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.suggest_folder_structure(
                    sample_emails=[{"subject": "Test", "from_addr": "test@test.com", "body": "..."}],
                )

                assert len(result) == 1
                assert result[0].name == "INBOX"


class TestRefineFolderStructure:
    """Tests for iterative folder refinement."""

    @pytest.mark.asyncio
    async def test_refine_folder_structure_success(self, ollama_config):
        mock_response = {
            "response": json.dumps({
                "categories": [
                    {"name": "Finance", "description": "Financial emails"},
                ],
                "email_assignments": [
                    {"email_num": 1, "category": "Finance"},
                ],
            })
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                categories, assignments = await client.refine_folder_structure(
                    sample_emails=[{"subject": "Invoice", "from_addr": "billing@example.com", "body": "..."}],
                    existing_categories=[],
                    batch_num=1,
                )

                assert len(categories) == 1
                assert categories[0].name == "Finance"
                assert len(assignments) == 1

    @pytest.mark.asyncio
    async def test_refine_folder_structure_preserves_existing(self, ollama_config):
        """Should preserve existing categories not in response."""
        mock_response = {
            "response": json.dumps({
                "categories": [
                    {"name": "NewCategory", "description": "New"},
                ],
                "email_assignments": [],
            })
        }

        existing = [SuggestedFolder(name="ExistingCategory", description="Existing", example_criteria=[])]

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                categories, _ = await client.refine_folder_structure(
                    sample_emails=[{"subject": "Test", "from_addr": "test@test.com", "body": "..."}],
                    existing_categories=existing,
                    batch_num=1,
                )

                names = [c.name for c in categories]
                assert "NewCategory" in names
                assert "ExistingCategory" in names

    @pytest.mark.asyncio
    async def test_refine_folder_structure_fallback_on_error(self, ollama_config):
        """Should return existing categories on parse failure."""
        mock_response = {"response": "invalid"}

        existing = [SuggestedFolder(name="Existing", description="...", example_criteria=[])]

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                categories, assignments = await client.refine_folder_structure(
                    sample_emails=[{"subject": "Test", "from_addr": "test@test.com", "body": "..."}],
                    existing_categories=existing,
                    batch_num=1,
                )

                assert categories == existing
                assert assignments == []


class TestNormalizeCategories:
    """Tests for category normalization."""

    @pytest.mark.asyncio
    async def test_normalize_single_category(self, ollama_config):
        """Should return single category unchanged."""
        async with OllamaClient(ollama_config) as client:
            categories = [SuggestedFolder(name="Single", description="Only one", example_criteria=[])]
            result, rename_map = await client.normalize_categories(categories)

            assert len(result) == 1
            assert result[0].name == "Single"
            assert rename_map == {"Single": "Single"}

    @pytest.mark.asyncio
    async def test_normalize_categories_success(self, ollama_config):
        mock_response = {
            "response": json.dumps({
                "consolidated_categories": [
                    {"name": "Finance", "description": "Merged financial", "merged_from": ["Finance", "Banking"]},
                ],
                "rename_map": {
                    "Finance": "Finance",
                    "Banking": "Finance",
                },
            })
        }

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                categories = [
                    SuggestedFolder(name="Finance", description="Financial", example_criteria=[]),
                    SuggestedFolder(name="Banking", description="Bank stuff", example_criteria=[]),
                ]

                result, rename_map = await client.normalize_categories(categories)

                assert len(result) == 1
                assert result[0].name == "Finance"
                assert rename_map["Banking"] == "Finance"


class TestRepairJson:
    """Tests for JSON repair functionality."""

    @pytest.mark.asyncio
    async def test_repair_json_success(self, ollama_config):
        # First call returns valid JSON
        mock_response = {"response": '{"repaired": true}'}

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.repair_json("{broken: json}")

                assert result == '{"repaired": true}'

    @pytest.mark.asyncio
    async def test_repair_json_failure(self, ollama_config):
        """Should return None if repair fails."""
        mock_response = {"response": "still broken"}

        async with OllamaClient(ollama_config) as client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_post.return_value = mock_resp

                result = await client.repair_json("{broken}")

                assert result is None
