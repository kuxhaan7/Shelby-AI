"""JSON-backed key-value memory Shelby can read and write at runtime."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NotesStore:
    """Persistent key-value store backed by a single JSON file."""

    def __init__(self, path: str | None = None) -> None:
        if path:
            self._path = Path(path)
        else:
            from ..paths import memory_file
            self._path = memory_file()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2, default=str))

    def write(self, key: str, value: str) -> None:
        self._data[key] = {
            "value": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def read(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        return f"{entry['value']}  (saved {entry['updated_at']})"

    def list_keys(self) -> list[str]:
        return sorted(self._data.keys())

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False

    def dump(self) -> str:
        if not self._data:
            return "Memory is empty."
        lines = [f"• {k}: {v['value']}" for k, v in self._data.items()]
        return "\n".join(lines)

    def as_list(self) -> list[dict[str, str]]:
        """Structured view of every stored fact, for the admin UI."""
        return [
            {"key": k, "value": v["value"], "updated_at": v.get("updated_at", "")}
            for k, v in sorted(self._data.items())
        ]
