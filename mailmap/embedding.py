"""Embedding client and vector math for the hybrid classifier."""

from __future__ import annotations

import array
import logging
import math

import httpx

from .config import OllamaConfig

logger = logging.getLogger("mailmap")


# --- Pure vector math (stdlib only, no numpy) ---

def vec_from_bytes(b: bytes) -> array.array:
    """Unpack a float32 BLOB into an array."""
    v: array.array = array.array("f")
    v.frombytes(b)
    return v


def vec_to_bytes(v: array.array) -> bytes:
    """Pack a float32 array into a BLOB."""
    return v.tobytes()


def cosine(a: array.array, b: array.array) -> float:
    """Cosine similarity between two float32 arrays."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def centroid(vecs: list[array.array]) -> array.array:
    """Element-wise mean of a list of same-length float32 arrays."""
    if not vecs:
        raise ValueError("Cannot compute centroid of empty list")
    n = len(vecs)
    dim = len(vecs[0])
    result: array.array = array.array("f", (0.0 for _ in range(dim)))
    for v in vecs:
        for i in range(dim):
            result[i] += v[i]
    for i in range(dim):
        result[i] /= n
    return result


# --- Async client ---

class EmbeddingClient:
    """Async client for Ollama /api/embed endpoint.

    Mirrors the OllamaClient context-manager pattern from llm.py.
    Uses config.embed_model (not config.model).
    """

    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> EmbeddingClient:
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(self.config.timeout_seconds),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async context manager.")
        return self._client

    async def embed(self, text: str) -> bytes:
        """Return the embedding for a single text as a raw bytes BLOB."""
        response = await self.client.post(
            "/api/embed",
            json={"model": self.config.embed_model, "input": text},
        )
        response.raise_for_status()
        data = response.json()
        vec = array.array("f", data["embeddings"][0])
        return vec.tobytes()

    async def embed_batch(self, texts: list[str]) -> list[bytes]:
        """Return embeddings for multiple texts in a single Ollama request."""
        if not texts:
            return []
        response = await self.client.post(
            "/api/embed",
            json={"model": self.config.embed_model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        return [array.array("f", row).tobytes() for row in data["embeddings"]]
