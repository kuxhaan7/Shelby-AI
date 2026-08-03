"""File discovery and ingestion for PDFs, Word docs, and other documents."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def read_pdf(path: str | Path) -> str:
    """Extract text from PDF file."""
    try:
        import PyPDF2
        text = []
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text.append(page.extract_text())
        return "\n".join(text)
    except ImportError:
        log.warning("PyPDF2 not installed, cannot read PDFs")
        return ""
    except Exception as e:
        log.error(f"Error reading PDF {path}: {e}")
        return ""


def read_docx(path: str | Path) -> str:
    """Extract text from Word document."""
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except ImportError:
        log.warning("python-docx not installed, cannot read DOCX files")
        return ""
    except Exception as e:
        log.error(f"Error reading DOCX {path}: {e}")
        return ""


def read_txt(path: str | Path) -> str:
    """Read plain text file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        log.error(f"Error reading TXT {path}: {e}")
        return ""


def read_csv(path: str | Path) -> str:
    """Read CSV file as text."""
    try:
        import pandas as pd
        df = pd.read_csv(path)
        return df.to_string()
    except Exception as e:
        log.error(f"Error reading CSV {path}: {e}")
        return ""


def read_json(path: str | Path) -> str:
    """Read JSON file as formatted text."""
    try:
        import json
        with open(path, "r") as f:
            data = json.load(f)
        return json.dumps(data, indent=2)
    except Exception as e:
        log.error(f"Error reading JSON {path}: {e}")
        return ""


def read_markdown(path: str | Path) -> str:
    """Read Markdown file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        log.error(f"Error reading Markdown {path}: {e}")
        return ""


def read_file(path: str | Path) -> tuple[str, bool]:
    """Read any supported file and return (content, success).

    Returns tuple of (content, success) where success indicates if file was read.
    """
    path = Path(path)
    if not path.exists():
        log.error(f"File not found: {path}")
        return "", False

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        content = read_pdf(path)
    elif suffix == ".docx":
        content = read_docx(path)
    elif suffix in (".txt", ".md", ".markdown"):
        content = read_markdown(path)
    elif suffix == ".csv":
        content = read_csv(path)
    elif suffix == ".json":
        content = read_json(path)
    else:
        # Try as plain text
        content = read_txt(path)

    success = bool(content and content.strip())
    return content, success


def find_files(
    directory: str | Path = ".",
    recursive: bool = True,
    extensions: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
) -> list[Path]:
    """Find all supported files in a directory.

    Args:
        directory: Root directory to search
        recursive: Search subdirectories
        extensions: File extensions to include (e.g., [".pdf", ".docx"])
        exclude_dirs: Directories to skip (e.g., [".git", "__pycache__"])

    Returns:
        List of file paths found
    """
    if extensions is None:
        extensions = [".pdf", ".docx", ".txt", ".md", ".csv", ".json"]

    if exclude_dirs is None:
        exclude_dirs = [".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"]

    directory = Path(directory)
    if not directory.is_dir():
        return []

    files = []
    pattern = "**/*" if recursive else "*"

    for path in directory.glob(pattern):
        # Skip excluded directories
        if any(excluded in path.parts for excluded in exclude_dirs):
            continue

        if path.is_file() and path.suffix.lower() in extensions:
            files.append(path)

    return sorted(files)


def ingest_files(
    rag_store: Any,
    directory: str | Path = ".",
    recursive: bool = True,
    max_files: int = 100,
    exclude_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """Ingest all files from a directory into the RAG store.

    Returns:
        Dict with ingestion results and stats
    """
    files = find_files(directory, recursive=recursive, exclude_dirs=exclude_dirs)
    files = files[:max_files]

    results = {
        "total_files": len(files),
        "ingested": 0,
        "failed": 0,
        "errors": [],
        "documents": [],
    }

    for file_path in files:
        try:
            content, success = read_file(file_path)
            if not success or not content.strip():
                results["failed"] += 1
                results["errors"].append(f"Empty or unreadable: {file_path.name}")
                continue

            # Chunk large files
            chunks = chunk_text(content, chunk_size=2000, overlap=200)

            for i, chunk in enumerate(chunks):
                if chunk.strip():
                    doc_id = rag_store.add(
                        text=chunk,
                        source=f"{file_path.name} (chunk {i+1}/{len(chunks)})",
                    )
                    results["documents"].append(doc_id)

            results["ingested"] += 1
            log.info(f"Ingested {file_path.name} ({len(chunks)} chunks)")

        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{file_path.name}: {str(e)}")
            log.error(f"Failed to ingest {file_path}: {e}")

    return results


def chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start = end - overlap

    return chunks
