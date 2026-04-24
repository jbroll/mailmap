"""Hybrid email classifier: centroid-based fast path with LLM fallback."""

from __future__ import annotations

import logging
from pathlib import Path

from .categories import get_category_descriptions, load_categories
from .config import Config
from .content import extract_email_summary
from .database import Database
from .embedding import EmbeddingClient, centroid, cosine, vec_from_bytes, vec_to_bytes
from .llm import ClassificationResult, OllamaClient

logger = logging.getLogger("mailmap")


class HybridClassifier:
    """Classifies emails via embedding centroids, falling back to LLM.

    Keeps an in-process centroid cache so each daemon loop iteration does not
    round-trip to the database for every email.  Centroids are invalidated
    explicitly (e.g., after sync detects manual moves) or implicitly when
    the sample_count stored in the DB changes.
    """

    def __init__(
        self,
        config: Config,
        db: Database,
        llm: OllamaClient,
        embedder: EmbeddingClient,
    ):
        self.config = config
        self.db = db
        self.llm = llm
        self.embedder = embedder
        # folder → (centroid_array, sample_count) — populated lazily
        self._centroid_cache: dict = {}
        self._cache_loaded = False

    def _load_centroids(self) -> None:
        """Load all centroids from DB into the in-process cache."""
        raw = self.db.get_all_centroids()
        self._centroid_cache = {
            folder: (vec_from_bytes(blob), count)
            for folder, (blob, count) in raw.items()
        }
        self._cache_loaded = True

    def invalidate_centroid(self, folder: str) -> None:
        """Remove a folder from the in-process cache (forces DB reload)."""
        self._centroid_cache.pop(folder, None)

    def recompute_centroid(self, folder: str) -> bool:
        """Recompute and persist the centroid for a folder from stored embeddings.

        Returns True if the centroid was updated, False if not enough data.
        """
        min_ex = self.config.embedding.min_examples
        rows = self.db.get_embeddings_by_classification(folder)
        if len(rows) < min_ex:
            logger.debug(f"Centroid skip: {folder} has {len(rows)} < {min_ex} embeddings")
            self.db.delete_centroid(folder)
            self._centroid_cache.pop(folder, None)
            return False

        vecs = [vec_from_bytes(blob) for _, blob in rows]
        c = centroid(vecs)
        blob = vec_to_bytes(c)
        self.db.upsert_centroid(folder, blob, len(vecs))
        self._centroid_cache[folder] = (c, len(vecs))
        logger.info(f"Centroid updated: {folder} ({len(vecs)} emails)")
        return True

    def _get_centroids(self) -> dict:
        """Return the in-process centroid cache, loading from DB if needed."""
        if not self._cache_loaded:
            self._load_centroids()
        return self._centroid_cache

    def _embedding_text(self, email_dict: dict) -> str:
        """Build the text to embed from an email dict."""
        cleaned = extract_email_summary(
            email_dict.get("subject", ""),
            email_dict.get("from_addr", email_dict.get("from", "")),
            email_dict.get("body", ""),
            max_body_length=500,
        )
        return f"Subject: {cleaned['subject']}\nFrom: {cleaned['from_addr']}\n\n{cleaned['body']}"

    async def classify(
        self,
        email_dict: dict,
        folder_descriptions: dict[str, str] | None = None,
    ) -> ClassificationResult:
        """Classify an email using centroids first, LLM on fallback.

        Args:
            email_dict: Must contain subject, from/from_addr, body, message_id.
            folder_descriptions: If None, loaded from config categories file.

        Returns:
            ClassificationResult (same type as OllamaClient.classify_email).
        """
        if folder_descriptions is None:
            categories = load_categories(Path(self.config.database.categories_file))
            folder_descriptions = get_category_descriptions(categories)

        message_id: str = email_dict.get("message_id", "")
        emb_cfg = self.config.embedding

        # --- Try fast path via centroids ---
        if emb_cfg.enabled:
            # Use cached embedding if available, otherwise compute and persist.
            blob = self.db.get_embedding(message_id) if message_id else None
            if blob is None:
                text = self._embedding_text(email_dict)
                try:
                    blob = await self.embedder.embed(text)
                    if message_id:
                        self.db.set_embedding(message_id, blob)
                except Exception as e:
                    logger.warning(f"Embedding failed, using LLM: {e}")
                    blob = None

            if blob is not None:
                result = self._centroid_classify(blob, folder_descriptions, emb_cfg)
                if result is not None:
                    return result

        # --- LLM fallback ---
        logger.debug("Embedding fast-path miss → LLM fallback")
        return await self.llm.classify_email(
            subject=email_dict.get("subject", ""),
            from_addr=email_dict.get("from_addr", email_dict.get("from", "")),
            body=email_dict.get("body", ""),
            folder_descriptions=folder_descriptions,
            attachments=email_dict.get("attachments"),
        )

    def _centroid_classify(
        self,
        blob: bytes,
        folder_descriptions: dict[str, str],
        emb_cfg,
    ) -> ClassificationResult | None:
        """Try to classify via centroid similarity. Returns None to trigger LLM."""
        centroids = self._get_centroids()
        valid = {
            f: c for f, (c, count) in centroids.items()
            if f in folder_descriptions and count >= emb_cfg.min_examples
        }
        if not valid:
            return None

        query = vec_from_bytes(blob)
        scores = sorted(
            ((f, cosine(query, c)) for f, c in valid.items()),
            key=lambda x: x[1],
            reverse=True,
        )

        top_folder, top_sim = scores[0]
        second_sim = scores[1][1] if len(scores) > 1 else 0.0
        margin = top_sim - second_sim

        if top_sim >= emb_cfg.min_similarity and margin >= emb_cfg.min_margin:
            secondary = [f for f, _ in scores[1:3] if _ > 0.3]
            logger.debug(
                f"[fast-path] {top_folder} sim={top_sim:.3f} margin={margin:.3f}"
            )
            return ClassificationResult(
                predicted_folder=top_folder,
                secondary_labels=secondary,
                confidence=float(top_sim),
            )

        logger.debug(
            f"[llm-fallback] top={top_folder} sim={top_sim:.3f} margin={margin:.3f}"
        )
        return None
