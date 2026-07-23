"""Chroma-backed vector store for Shelby's long-term RAG memory."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings


class RagStore:
    """Thin wrapper around ChromaDB for document storage and semantic retrieval."""

    def __init__(self, persist_dir: str | None = None, collection: str = "shelby") -> None:
        from ..paths import chroma_dir
        persist_dir = persist_dir or str(chroma_dir())
        Path(persist_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._col = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Write ────────────────────────────────────────────────────────────────

    def add(self, text: str, source: str = "unknown", doc_id: str | None = None) -> str:
        """Add a single passage to the store. Returns the assigned doc_id."""
        doc_id = doc_id or str(uuid.uuid4())
        self._col.add(
            documents=[text],
            metadatas=[{"source": source}],
            ids=[doc_id],
        )
        return doc_id

    def add_batch(self, texts: list[str], sources: list[str] | None = None) -> list[str]:
        """Add multiple passages at once."""
        if sources is None:
            sources = ["unknown"] * len(texts)
        ids = [str(uuid.uuid4()) for _ in texts]
        self._col.add(
            documents=texts,
            metadatas=[{"source": s} for s in sources],
            ids=ids,
        )
        return ids

    # ── Read ─────────────────────────────────────────────────────────────────

    def query(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        """Return top-n passages ranked by cosine similarity."""
        n_results = min(n_results, self._col.count() or 1)
        if self._col.count() == 0:
            return []
        raw = self._col.query(query_texts=[query], n_results=n_results)
        docs = raw["documents"][0]
        metas = raw["metadatas"][0]
        distances = raw["distances"][0]
        return [
            {"text": d, "source": m.get("source", ""), "score": round(1 - dist, 4)}
            for d, m, dist in zip(docs, metas, distances)
        ]

    def count(self) -> int:
        return self._col.count()
