"""Tests for embedding helpers and HybridClassifier threshold logic."""

from __future__ import annotations

import array
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from mailmap.config import Config, DatabaseConfig, EmbeddingConfig, ImapConfig, OllamaConfig
from mailmap.database import Database, Email
from mailmap.embedding import centroid, cosine, vec_from_bytes, vec_to_bytes
from mailmap.llm import ClassificationResult

# --- Pure math helpers ---

def make_vec(*values: float) -> array.array:
    return array.array("f", values)


def test_vec_round_trip():
    original = make_vec(1.0, 2.0, 3.0, -0.5)
    assert vec_from_bytes(vec_to_bytes(original)) == original


def test_cosine_identical():
    v = make_vec(1.0, 0.0, 0.0)
    assert cosine(v, v) == pytest.approx(1.0, abs=1e-5)


def test_cosine_orthogonal():
    a = make_vec(1.0, 0.0)
    b = make_vec(0.0, 1.0)
    assert cosine(a, b) == pytest.approx(0.0, abs=1e-5)


def test_cosine_opposite():
    a = make_vec(1.0, 0.0)
    b = make_vec(-1.0, 0.0)
    assert cosine(a, b) == pytest.approx(-1.0, abs=1e-5)


def test_cosine_zero_vector():
    a = make_vec(0.0, 0.0)
    b = make_vec(1.0, 0.0)
    assert cosine(a, b) == 0.0


def test_centroid_single():
    v = make_vec(2.0, 4.0)
    c = centroid([v])
    assert c[0] == pytest.approx(2.0)
    assert c[1] == pytest.approx(4.0)


def test_centroid_two():
    a = make_vec(0.0, 0.0)
    b = make_vec(2.0, 4.0)
    c = centroid([a, b])
    assert c[0] == pytest.approx(1.0)
    assert c[1] == pytest.approx(2.0)


def test_centroid_empty_raises():
    with pytest.raises(ValueError):
        centroid([])


# --- HybridClassifier threshold logic ---

def _make_config(min_similarity=0.55, min_margin=0.05, min_examples=2, enabled=True):
    return Config(
        imap=ImapConfig(host="imap.example.com", username="u", password="p"),
        ollama=OllamaConfig(),
        database=DatabaseConfig(),
        embedding=EmbeddingConfig(
            enabled=enabled,
            min_similarity=min_similarity,
            min_margin=min_margin,
            min_examples=min_examples,
        ),
    )


def _make_unit_vec(angle_deg: float, dim: int = 768) -> array.array:
    """Unit vector with first two dims set by angle, rest zero."""
    v = array.array("f", [0.0] * dim)
    rad = math.radians(angle_deg)
    v[0] = math.cos(rad)
    v[1] = math.sin(rad)
    return v


@pytest.fixture
def test_db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.connect()
    db.init_schema()
    # Seed two folders with embeddings.
    for msg_id, cls, angle in [
        ("<a@x>", "Financial", 0),
        ("<b@x>", "Financial", 5),
        ("<c@x>", "Personal", 90),
        ("<d@x>", "Personal", 85),
    ]:
        email = Email(
            message_id=msg_id, folder_id="INBOX",
            subject="s", from_addr="f@f", mbox_path="",
            classification=cls,
        )
        db.insert_email(email)
        db.set_embedding(msg_id, vec_to_bytes(_make_unit_vec(angle)))
        db.update_classification(msg_id, cls, 0.9)
    yield db
    db.close()


@pytest.mark.asyncio
async def test_fast_path_taken(test_db):
    """When query is clearly closest to Financial, fast path returns Financial."""
    from mailmap.classifier import HybridClassifier

    config = _make_config()

    # Build centroids.
    for cls, _angle in [("Financial", 2.5), ("Personal", 87.5)]:
        rows = test_db.get_embeddings_by_classification(cls)
        vecs = [vec_from_bytes(b) for _, b in rows]
        c = centroid(vecs)
        test_db.upsert_centroid(cls, vec_to_bytes(c), len(vecs))

    mock_llm = MagicMock()
    mock_embedder = MagicMock()
    query_blob = vec_to_bytes(_make_unit_vec(1))  # close to Financial centroid

    mock_embedder.embed = AsyncMock(return_value=query_blob)

    classifier = HybridClassifier(config, test_db, mock_llm, mock_embedder)

    folder_descriptions = {"Financial": "Bank emails", "Personal": "Personal emails"}
    email_dict = {
        "message_id": "<query@x>",
        "subject": "Your statement",
        "from": "bank@bank.com",
        "body": "Your balance is $100",
    }

    result = await classifier.classify(email_dict, folder_descriptions=folder_descriptions)

    assert result.predicted_folder == "Financial"
    assert result.confidence >= 0.55
    mock_llm.classify_email.assert_not_called()


@pytest.mark.asyncio
async def test_llm_fallback_on_low_margin(test_db):
    """When the query is equidistant between folders, LLM fallback is used."""
    from mailmap.classifier import HybridClassifier

    config = _make_config(min_margin=0.5)  # very high margin → always fallback

    for cls, _angle in [("Financial", 2.5), ("Personal", 87.5)]:
        rows = test_db.get_embeddings_by_classification(cls)
        vecs = [vec_from_bytes(b) for _, b in rows]
        c = centroid(vecs)
        test_db.upsert_centroid(cls, vec_to_bytes(c), len(vecs))

    llm_result = ClassificationResult(
        predicted_folder="Financial", secondary_labels=[], confidence=0.7
    )
    mock_llm = MagicMock()
    mock_llm.classify_email = AsyncMock(return_value=llm_result)
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(return_value=vec_to_bytes(_make_unit_vec(45)))

    classifier = HybridClassifier(config, test_db, mock_llm, mock_embedder)
    folder_descriptions = {"Financial": "Bank emails", "Personal": "Personal emails"}
    email_dict = {
        "message_id": "<query2@x>",
        "subject": "hello",
        "from": "x@x.com",
        "body": "hi",
    }

    result = await classifier.classify(email_dict, folder_descriptions=folder_descriptions)

    mock_llm.classify_email.assert_called_once()
    assert result.predicted_folder == "Financial"


@pytest.mark.asyncio
async def test_llm_fallback_when_embedding_disabled(test_db):
    """When embedding is disabled in config, always use LLM."""
    from mailmap.classifier import HybridClassifier

    config = _make_config(enabled=False)
    llm_result = ClassificationResult(
        predicted_folder="Personal", secondary_labels=[], confidence=0.8
    )
    mock_llm = MagicMock()
    mock_llm.classify_email = AsyncMock(return_value=llm_result)
    mock_embedder = MagicMock()

    classifier = HybridClassifier(config, test_db, mock_llm, mock_embedder)
    folder_descriptions = {"Financial": "desc", "Personal": "desc"}
    email_dict = {"message_id": "<q3@x>", "subject": "hi", "from": "a@b", "body": ""}

    result = await classifier.classify(email_dict, folder_descriptions=folder_descriptions)

    mock_llm.classify_email.assert_called_once()
    assert result.predicted_folder == "Personal"
    mock_embedder.embed.assert_not_called()


@pytest.mark.asyncio
async def test_llm_fallback_cold_start(test_db, tmp_path):
    """When no centroids exist, fall through to LLM."""
    from mailmap.classifier import HybridClassifier

    # Empty database — no centroids.
    empty_db = Database(tmp_path / "empty.db")
    empty_db.connect()
    empty_db.init_schema()

    config = _make_config()
    llm_result = ClassificationResult(
        predicted_folder="Newsletters", secondary_labels=[], confidence=0.6
    )
    mock_llm = MagicMock()
    mock_llm.classify_email = AsyncMock(return_value=llm_result)
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(return_value=vec_to_bytes(_make_unit_vec(0)))

    classifier = HybridClassifier(config, empty_db, mock_llm, mock_embedder)
    folder_descriptions = {"Newsletters": "desc"}
    email_dict = {"message_id": "<q4@x>", "subject": "news", "from": "n@n", "body": ""}

    result = await classifier.classify(email_dict, folder_descriptions=folder_descriptions)

    mock_llm.classify_email.assert_called_once()
    assert result.predicted_folder == "Newsletters"
    empty_db.close()
