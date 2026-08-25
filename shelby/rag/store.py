"""Chroma-backed vector store for Shelby's long-term RAG memory."""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

log = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Cheap BM25 tokenizer — lowercase, strip punctuation, split alphanum runs.
    Good enough for the exact-match failures dense retrieval trips on
    (filenames, error codes, function names). Not intended for prose analysis."""
    return _TOKEN_RE.findall((text or "").lower())


def _rrf_merge(
    dense: list[dict], bm25: list[dict], k: int = 60
) -> list[dict]:
    """Reciprocal Rank Fusion of two ranked candidate lists.

    RRF combines rankings without normalizing scores across retrievers (their
    scales are incompatible). For each doc, score = sum over retrievers of
    1 / (k + rank). k=60 is the widely-used default; higher k dampens the
    boost from being #1 in either list, giving more docs a chance to combine.

    Both inputs are lists of dicts that must include "id". A doc appearing in
    both lists is scored once with the summed rank contribution.
    """
    scores: dict[str, float] = {}
    lookup: dict[str, dict] = {}

    for rank, doc in enumerate(dense):
        did = doc["id"]
        scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank + 1)
        lookup[did] = doc

    for rank, doc in enumerate(bm25):
        did = doc["id"]
        scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank + 1)
        # BM25 may surface a doc dense missed entirely — remember it.
        if did not in lookup:
            lookup[did] = doc

    sorted_ids = sorted(scores.keys(), key=lambda d: -scores[d])
    return [{**lookup[did], "rrf_score": round(scores[did], 6)} for did in sorted_ids]


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


def _pick_reranker():
    """Return a callable (query, docs, top_n) -> [(index, score), ...] or None.

    Cross-encoder rerank pass on top of the bi-encoder retrieval. The
    embedder retrieves K candidates by cosine similarity (fast, coarse); the
    reranker rescores each (query, candidate) pair jointly (slower, sharper)
    and picks the real top-N.

    Voyage's rerank-2 is the current default because we're already wired to
    Voyage for embeddings — one key, one dashboard. Disable entirely with
    SHELBY_RERANK=false.
    """
    if os.getenv("SHELBY_RERANK", "true").strip().lower() in ("false", "0", "no", "off"):
        log.info("RAG reranker: disabled via SHELBY_RERANK")
        return None

    voyage_key = os.getenv("VOYAGE_API_KEY")
    if voyage_key:
        try:
            import voyageai
            client = voyageai.Client(api_key=voyage_key)
            model = os.getenv("VOYAGE_RERANK_MODEL", "rerank-2")

            def rerank(query: str, docs: list[str], top_n: int):
                result = client.rerank(
                    query=query, documents=docs, model=model, top_k=top_n
                )
                return [(int(r.index), float(r.relevance_score)) for r in result.results]

            log.info("RAG reranker: Voyage %s", model)
            return rerank
        except Exception as exc:
            log.warning("Voyage reranker unavailable (%s); rerank disabled", exc)

    log.info("RAG reranker: none")
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
        # Reranker constructed once at startup; None if disabled or unavailable.
        # query() feature-tests on it instead of rebuilding a client per call.
        self._reranker = _pick_reranker()

        # BM25 index for the lexical half of hybrid retrieval. Built lazily
        # from the collection contents on first query, and invalidated on
        # every write. Disable entirely with SHELBY_HYBRID=false — dense-only
        # retrieval is still available as before.
        self._bm25 = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_metas: list[dict] = []
        self._hybrid_enabled = os.getenv("SHELBY_HYBRID", "true").strip().lower() not in (
            "false", "0", "no", "off",
        )
        log.info(
            "RAG collection=%s count=%d hybrid=%s",
            collection_name, self._col.count(), self._hybrid_enabled,
        )

    # ── BM25 lifecycle ──────────────────────────────────────────────────────

    def _rebuild_bm25(self) -> None:
        """Load every doc out of Chroma and rebuild the in-memory BM25 index.
        Skipped if hybrid is off, rank_bm25 isn't installed, or the store is
        empty. O(N) — fine for demo/solo scale (thousands of docs); if the
        store grows past that, invalidate more selectively than we do today."""
        if not self._hybrid_enabled:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            log.info("rank_bm25 not installed; hybrid disabled")
            self._bm25 = None
            return
        if self._col.count() == 0:
            self._bm25 = None
            return

        raw = self._col.get(include=["documents", "metadatas"])
        self._bm25_ids = list(raw["ids"])
        self._bm25_docs = [d or "" for d in raw["documents"]]
        self._bm25_metas = [m or {} for m in raw["metadatas"]]
        tokenized = [_tokenize(d) for d in self._bm25_docs]
        self._bm25 = BM25Okapi(tokenized)

    def _invalidate_bm25(self) -> None:
        """Mark BM25 stale. Next query() rebuilds it. Cheap."""
        self._bm25 = None

    # ── Write ────────────────────────────────────────────────────────────────

    def add(self, text: str, source: str = "unknown", doc_id: str | None = None) -> str:
        """Add a single passage to the store. Returns the assigned doc_id."""
        doc_id = doc_id or str(uuid.uuid4())
        self._col.add(
            documents=[text],
            metadatas=[{"source": source}],
            ids=[doc_id],
        )
        self._invalidate_bm25()
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
        self._invalidate_bm25()
        return ids

    # ── Read ─────────────────────────────────────────────────────────────────

    def query(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        """Hybrid dense + BM25 retrieval with optional rerank.

        Three layers, each optional:

          1. Dense (always): Chroma cosine top-K. Fast, semantic.
          2. BM25 (if SHELBY_HYBRID != false and rank_bm25 installed):
             lexical top-K over the same collection. Catches exact-string
             queries (filenames, error codes) dense embedders fumble.
          3. Rerank (if a reranker is configured): cross-encoder rescore
             on the merged pool. Sharper top-N than either retriever alone.

        Dense and BM25 candidate lists are merged with Reciprocal Rank Fusion
        (k=60) before rerank. If rerank fails or is disabled, we return the
        RRF top-N so retrieval degrades gracefully rather than going offline.
        """
        total = self._col.count()
        if total == 0:
            return []

        # Fetch pool size: wider when a reranker is available (it needs
        # candidates to work with), otherwise just n_results.
        fetch_n = min(n_results, total)
        if self._reranker is not None:
            multiplier = int(os.getenv("SHELBY_RERANK_FETCH_MULTIPLIER", "5"))
            fetch_n = min(max(n_results, n_results * multiplier), total)

        # ── Dense (always) ─────────────────────────────────────────────────
        raw = self._col.query(query_texts=[query], n_results=fetch_n)
        dense_ids = raw["ids"][0]
        dense_docs = raw["documents"][0]
        dense_metas = raw["metadatas"][0]
        dense_distances = raw["distances"][0]

        dense_hits = [
            {
                "id": did,
                "text": doc,
                "source": (m or {}).get("source", ""),
                "cosine_score": round(1 - dist, 4),
            }
            for did, doc, m, dist in zip(dense_ids, dense_docs, dense_metas, dense_distances)
        ]

        # ── BM25 (if enabled + available) ──────────────────────────────────
        bm25_hits: list[dict] = []
        if self._hybrid_enabled:
            if self._bm25 is None:
                self._rebuild_bm25()
            if self._bm25 is not None:
                scores = self._bm25.get_scores(_tokenize(query))
                # Top fetch_n by score, dropping zeros (BM25 returns 0 for
                # docs with zero overlapping terms — pure noise).
                ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:fetch_n]
                bm25_hits = [
                    {
                        "id": self._bm25_ids[i],
                        "text": self._bm25_docs[i],
                        "source": (self._bm25_metas[i] or {}).get("source", ""),
                        "bm25_score": round(float(scores[i]), 4),
                    }
                    for i in ranked
                    if scores[i] > 0
                ]

        # ── Merge (RRF) ────────────────────────────────────────────────────
        merged = _rrf_merge(dense_hits, bm25_hits, k=60) if bm25_hits else dense_hits

        # ── Rerank (or fall through to RRF top-N) ──────────────────────────
        if self._reranker is None or len(merged) <= n_results:
            return [
                {"text": c["text"], "source": c.get("source", ""),
                 "score": c.get("rrf_score", c.get("cosine_score", 0.0))}
                for c in merged[:n_results]
            ]

        try:
            reranked = self._reranker(query, [c["text"] for c in merged], n_results)
            return [
                {"text": merged[idx]["text"],
                 "source": merged[idx].get("source", ""),
                 "score": round(score, 4)}
                for idx, score in reranked
            ]
        except Exception as exc:
            log.warning("Rerank failed (%s); returning RRF top-%d", exc, n_results)
            return [
                {"text": c["text"], "source": c.get("source", ""),
                 "score": c.get("rrf_score", c.get("cosine_score", 0.0))}
                for c in merged[:n_results]
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
