"""Centralised filesystem paths for all of Shelby's writable state.

Everything Shelby persists — memory, learned lessons, usage stats, scheduled
tasks, the RAG vector store, learned skills, Kaggle downloads — lives under a
single base directory. Point SHELBY_DATA_DIR at a mounted Railway volume
(e.g. /data) and all of it survives redeploys with no other changes.

Each path also keeps its own specific env override for backwards compatibility.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Umbrella base dir. Default "data" (repo-relative) for local dev; set to a
# mounted volume path like "/data" on Railway to persist across redeploys.
DATA_DIR = Path(os.getenv("SHELBY_DATA_DIR", "data"))

# The skills committed into the image travel here (repo/data/skills). Runtime
# may use a different (volume-backed) skills dir, seeded from this one.
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"


def _p(specific_env: str, *parts: str) -> Path:
    override = os.getenv(specific_env)
    return Path(override) if override else DATA_DIR.joinpath(*parts)


def memory_file() -> Path:
    return _p("SHELBY_MEMORY_FILE", "memory.json")


def lessons_file() -> Path:
    return _p("SHELBY_LESSONS_FILE", "lessons.json")


def usage_file() -> Path:
    return _p("SHELBY_USAGE_FILE", "usage_stats.json")


def tasks_file() -> Path:
    return _p("SHELBY_TASKS_FILE", "scheduled_tasks.json")


def skills_dir() -> Path:
    return _p("SHELBY_SKILLS_DIR", "skills")


def chroma_dir() -> Path:
    return _p("CHROMA_PERSIST_DIR", "chroma")


def kaggle_dir() -> Path:
    return _p("SHELBY_KAGGLE_DIR", "kaggle_downloads")


def seed_bundled_skills(target: Path | None = None) -> int:
    """Copy image-bundled skills into the (possibly volume-backed) skills dir.

    On a fresh volume the runtime skills dir starts empty, which would hide the
    skills shipped with the image. Copy any missing bundled skill into it so
    both bundled and later-learned skills coexist and persist. Returns the count
    of files copied. No-op when the target is the bundled dir itself.
    """
    target = target or skills_dir()
    target.mkdir(parents=True, exist_ok=True)
    if target.resolve() == BUNDLED_SKILLS_DIR.resolve() or not BUNDLED_SKILLS_DIR.exists():
        return 0
    copied = 0
    for src in BUNDLED_SKILLS_DIR.glob("*.py"):
        dst = target / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
    return copied
