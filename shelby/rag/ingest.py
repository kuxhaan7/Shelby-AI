"""Document ingestion — splits text into chunks and loads them into RagStore."""

from __future__ import annotations

import re
from pathlib import Path

from .store import RagStore


def _chunk(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into overlapping word-count chunks."""
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + size]))
        i += size - overlap
    return [c for c in chunks if c.strip()]


def ingest_text(store: RagStore, text: str, source: str = "manual") -> int:
    """Chunk raw text and add to store. Returns number of chunks added."""
    chunks = _chunk(text)
    if not chunks:
        return 0
    store.add_batch(chunks, sources=[source] * len(chunks))
    return len(chunks)


def ingest_file(store: RagStore, path: str | Path) -> int:
    """Read a plain-text or markdown file and ingest it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    return ingest_text(store, text, source=path.name)


def ingest_workspace(store: RagStore, workspace_dir: str | Path = "./workspace") -> dict[str, int]:
    """Ingest all .md files from Shelby's workspace directory."""
    workspace_dir = Path(workspace_dir)
    results: dict[str, int] = {}
    for md_file in workspace_dir.glob("**/*.md"):
        n = ingest_file(store, md_file)
        results[md_file.name] = n
    return results
