"""Unit tests for RAG store and ingestion — no API key needed."""

import tempfile
import pytest
from shelby.rag.store import RagStore
from shelby.rag.ingest import _chunk, ingest_text


# ── Chunker ──────────────────────────────────────────────────────────────────

def test_chunk_short_text():
    chunks = _chunk("hello world", size=512, overlap=64)
    assert chunks == ["hello world"]


def test_chunk_empty():
    assert _chunk("") == []
    assert _chunk("   ") == []


def test_chunk_splits_long_text():
    words = ["word"] * 600
    text = " ".join(words)
    chunks = _chunk(text, size=512, overlap=64)
    assert len(chunks) > 1
    # First chunk is full size; last chunk is the remainder
    assert len(chunks[0].split()) == 512
    assert len(chunks[-1].split()) <= 512


def test_chunk_overlap():
    words = list(map(str, range(100)))
    text = " ".join(words)
    chunks = _chunk(text, size=50, overlap=10)
    # Second chunk should start at index 40 (50-10)
    second_start = chunks[1].split()[0]
    assert second_start == "40"


# ── RagStore ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return RagStore(persist_dir=str(tmp_path / "chroma"))


def test_store_add_and_count(store):
    assert store.count() == 0
    store.add("hello world", source="test")
    assert store.count() == 1


def test_store_add_batch(store):
    store.add_batch(["doc one", "doc two", "doc three"], sources=["a", "b", "c"])
    assert store.count() == 3


def test_store_query_returns_results(store):
    store.add("Shelby is an AI agent built with Claude", source="readme")
    store.add("FastAPI powers the REST endpoints", source="readme")
    results = store.query("What is Shelby?", n_results=1)
    assert len(results) == 1
    assert "source" in results[0]
    assert "score" in results[0]
    assert "text" in results[0]


def test_store_query_empty_store(store):
    assert store.query("anything") == []


def test_store_score_range(store):
    store.add("Claude API tool use with function calling", source="test")
    results = store.query("Claude function calling", n_results=1)
    assert 0 <= results[0]["score"] <= 1


# ── Ingest ────────────────────────────────────────────────────────────────────

def test_ingest_text(store):
    n = ingest_text(store, "Shelby uses ChromaDB for persistent vector storage.", source="test")
    assert n == 1
    assert store.count() == 1


def test_ingest_empty(store):
    n = ingest_text(store, "   ", source="test")
    assert n == 0
    assert store.count() == 0


def test_ingest_long_text(store):
    text = " ".join(["word"] * 1000)
    n = ingest_text(store, text, source="test")
    assert n > 1


def test_ingest_file(store, tmp_path):
    from shelby.rag.ingest import ingest_file
    f = tmp_path / "sample.md"
    f.write_text("# Shelby\n\nAn autonomous agent built on the Claude API.")
    n = ingest_file(store, f)
    assert n >= 1
