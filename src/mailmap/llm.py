"""Ollama LLM integration for email classification."""

import json
import httpx
from dataclasses import dataclass

from .config import OllamaConfig


@dataclass
class ClassificationResult:
    predicted_folder: str
    secondary_labels: list[str]
    confidence: float


@dataclass
class FolderDescription:
    folder_id: str
    description: str


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OllamaClient":
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(self.config.timeout_seconds),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async context manager.")
        return self._client

    async def _generate(self, prompt: str) -> str:
        """Send a generation request to Ollama."""
        response = await self.client.post(
            "/api/generate",
            json={
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["response"]

    async def classify_email(
        self,
        subject: str,
        from_addr: str,
        body: str,
        folder_descriptions: dict[str, str],
    ) -> ClassificationResult:
        """Classify an email into one of the available folders."""
        folders_text = "\n".join(
            f"- {folder_id}: {desc}" for folder_id, desc in folder_descriptions.items()
        )

        prompt = f"""You are an email classification assistant. Classify the following email into the most appropriate folder.

Available folders and their descriptions:
{folders_text}

Email to classify:
From: {from_addr}
Subject: {subject}
Body: {body[:2000]}

Respond with a JSON object containing:
- "predicted_folder": the folder_id that best matches this email
- "secondary_labels": a list of up to 3 relevant category labels
- "confidence": a number from 0 to 1 indicating classification confidence

JSON response:"""

        response_text = await self._generate(prompt)

        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])
                return ClassificationResult(
                    predicted_folder=data.get("predicted_folder", "INBOX"),
                    secondary_labels=data.get("secondary_labels", []),
                    confidence=float(data.get("confidence", 0.5)),
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        return ClassificationResult(
            predicted_folder="INBOX",
            secondary_labels=[],
            confidence=0.0,
        )

    async def generate_folder_description(
        self, folder_name: str, sample_emails: list[dict[str, str]]
    ) -> FolderDescription:
        """Generate a description for a folder based on sample emails."""
        samples_text = ""
        for i, email in enumerate(sample_emails[:5], 1):
            samples_text += f"""
Email {i}:
  From: {email.get('from_addr', 'unknown')}
  Subject: {email.get('subject', 'no subject')}
  Preview: {email.get('body', '')[:200]}
"""

        prompt = f"""Analyze the following sample emails from the folder "{folder_name}" and generate a brief description of what types of emails this folder contains.

Sample emails:
{samples_text}

Provide a concise 1-2 sentence description of this folder's purpose and the types of emails it contains. Be specific about the content patterns you observe.

Description:"""

        response_text = await self._generate(prompt)
        description = response_text.strip()

        return FolderDescription(folder_id=folder_name, description=description)
