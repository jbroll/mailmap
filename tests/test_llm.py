"""Tests for LLM module."""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from mailmap.config import OllamaConfig
from mailmap.llm import OllamaClient, ClassificationResult, FolderDescription


@pytest.fixture
def ollama_config():
    return OllamaConfig(
        base_url="http://localhost:11434",
        model="test-model",
        timeout_seconds=30,
    )


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

                # Should return default values on parse failure
                assert result.predicted_folder == "INBOX"
                assert result.secondary_labels == []
                assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_email_extracts_json_from_text(self, ollama_config):
        # LLM might return JSON embedded in text
        mock_response = {
            "response": 'Based on analysis, here is the classification:\n{"predicted_folder": "Work", "secondary_labels": ["Meeting"], "confidence": 0.85}\nThis email appears to be work-related.'
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
    async def test_generate_folder_description(self, ollama_config):
        mock_response = {
            "response": "Contains order confirmations, shipping notifications, and purchase receipts from online retailers."
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
                        {"subject": "Shipping update", "from_addr": "fedex@fedex.com", "body": "Package..."},
                    ],
                )

                assert result.folder_id == "Receipts"
                assert "order" in result.description.lower() or "receipt" in result.description.lower()
