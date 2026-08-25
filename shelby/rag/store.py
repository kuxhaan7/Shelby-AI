"""Chroma-backed vector store for Shelby's long-term RAG memory."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

log = logging.getLogger(__name__)


def _pick_embedder():
    """Choose the best embedding backend based on env keys, in this order:

      1. Voyage voyage-3 if VOYAGE_API_KEY is set. Anthropic's recommended
         embedding pairing for Claude, ~$0.06 per 1M tokens, 1024-dim.
      2. OpenAI text-embedding-3-small if OPENAI_API_KEY is set. Industry
         baseline, ~$0.02 per 1M tokens, 1536-dim.
      3. Chroma's default (all-MiniLM-L6-v2, local sentence-transformers,
         384-dim). Free, no API call, mediocre quality on domain text.

    Returns None to let Chroma use its default; otherwise an EmbeddingFunction.
    Any failure to construct the API-backed embedder falls through to the
    next option so a bad key doesn't take retrieval offline.
    """
    from chromadb.utils import embedding_functions

    voyage_key = os.getenv("VOYAGE_API_KEY")
    if voyage_key:
        try:
            ef = embedding_functions.VoyageAIEmbeddingFunction(
                api_key=voyage_key,
                model_name=os.getenv("VOYAGE_MODEL", "voyage-3"),
            )
            log.info("RAG embedder: Voyage %s", os.getenv("VOYAGE_MODEL", "voyage-3"))
            return ef
        except Exception as exc:
            log.warning("Voyage embedder unavailable (%s); trying next", exc)

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            ef = embedding_functions.OpenAIEmbeddingFunction(
                api_key=openai_key,
                model_name=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            )
            log.info("RAG embedder: OpenAI %s",
                     os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
            return ef
        except Exception as exc:
            log.warning("OpenAI embedder unavailable (%s); falling back to default", exc)

    log.info("RAG embedder: Chroma default (all-MiniLM-L6-v2, local)")
    return None


def _collection_for_embedder(embedder) -> str:
    """Derive a collection name that encodes the embedder identity.

    Chroma stores embeddings as fixed-dim vectors and rejects mixed dimensions
    in one collection, so switching embedders on an existing collection would
    error on the next insert. Namespacing by embedder sidesteps this: a switch
    from default (384-dim) to Voyage (1024-dim) opens a fresh collection.
    Old data stays on disk but is invisible until you re-ingest or switch
    the key back — an honest migration story rather than a silent break.
    """
    override = os.getenv("SHELBY_RAG_COLLECTION")
    if override:
        return override
    if embedder is None:
        return "shelby"  # existing default; backwards-compatible with prior data
    cls = type(embedder).__name__.lower()
    if "voyage" in cls:
        return "shelby-voyage"
    if "openai" in cls:
        return "shelby-openai"
    return "shelby-custom"


class RagStore:
    """Thin wrapper around ChromaDB for document storage and semantic retrieval."""

    def __init__(self, persist_dir: str | None = None, collection: str | None = None) -> None:
        from ..paths import chroma_dir
        persist_dir = persist_dir or str(chroma_dir())
        Path(persist_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        embedder = _pick_embedder()
        collection_name = collection or _collection_for_embedder(embedder)
        # Pass embedding_function only if we picked a non-default one; passing
        # None to Chroma is equivalent, but omitting the kwarg keeps behavior
        # identical to older Chroma versions where the parameter didn't exist.
        kwargs: dict[str, Any] = {
            "name": collection_name,
            "metadata": {"hnsw:space": "cosine"},
        }
        if embedder is not None:
            kwargs["embedding_function"] = embedder
        self._col = self._client.get_or_create_collection(**kwargs)
        log.info("RAG collection=%s count=%d", collection_name, self._col.count())

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

    def get_all(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Get all documents from the store. Returns text, source, and ID for each."""
        if self._col.count() == 0:
            return []
        raw = self._col.get(include=["documents", "metadatas"], limit=limit)
        return [
            {"id": id, "text": doc, "source": (meta or {}).get("source", "unknown")}
            for id, doc, meta in zip(raw["ids"], raw["documents"], raw["metadatas"])
        ]

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
