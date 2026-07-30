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

    def graph_data(self, top_k: int = 3, min_similarity: float = 0.35, max_nodes: int = 500) -> dict[str, Any]:
        """Node-link graph of stored passages, connected by embedding similarity.

        Each passage is a node; an edge connects it to its top_k most similar
        other passages above min_similarity, mirroring how a linked-notes
        graph (Obsidian's graph view) reads, except the "links" here are
        computed from semantic similarity rather than authored wikilinks.
        """
        if self._col.count() == 0:
            return {"nodes": [], "edges": []}

        raw = self._col.get(include=["embeddings", "documents", "metadatas"], limit=max_nodes)
        ids = raw["ids"]
        docs = raw["documents"]
        metas = raw["metadatas"]
        embeddings = raw["embeddings"]

        import numpy as np
        vecs = np.asarray(embeddings, dtype=float)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1e-9
        unit = vecs / norms
        sims = unit @ unit.T

        nodes = []
        for i, doc_id in enumerate(ids):
            text = docs[i] or ""
            label = text[:80] + ("..." if len(text) > 80 else "")
            nodes.append({"id": doc_id, "label": label, "source": (metas[i] or {}).get("source", "unknown")})

        edges = []
        seen: set[tuple[int, int]] = set()
        for i in range(len(ids)):
            order = sims[i].argsort()[::-1]
            picked = 0
            for j in order:
                j = int(j)
                if j == i:
                    continue
                score = float(sims[i][j])
                if score < min_similarity:
                    break
                key = (i, j) if i < j else (j, i)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": ids[i], "target": ids[j], "weight": round(score, 3)})
                picked += 1
                if picked >= top_k:
                    break

        return {"nodes": nodes, "edges": edges}
