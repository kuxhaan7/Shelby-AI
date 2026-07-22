"""Skill registry — load, save, list, and execute Shelby's learned skills."""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path
from typing import Any


class SkillRegistry:
    """Manages skills stored as .py files in a skills directory."""

    def __init__(self, skills_dir: str | None = None) -> None:
        self._dir = Path(skills_dir or os.getenv("SHELBY_SKILLS_DIR", "./data/skills"))
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list(self) -> list[dict[str, str]]:
        skills = []
        for f in sorted(self._dir.glob("*.py")):
            desc = self._read_description(f)
            skills.append({"name": f.stem, "description": desc, "file": str(f)})
        return skills

    def exists(self, name: str) -> bool:
        return (self._dir / f"{name}.py").exists()

    # ── Writing ───────────────────────────────────────────────────────────────

    def save(self, name: str, description: str, code: str) -> str:
        """Save a new skill. The code must define a run(**kwargs) -> str function."""
        name = name.replace(" ", "_").lower()
        path = self._dir / f"{name}.py"
        header = f'"""Skill: {name}\nDescription: {description}\n"""\n\n'
        path.write_text(header + code)
        return str(path)

    # ── Execution ─────────────────────────────────────────────────────────────

    def run(self, name: str, kwargs: dict[str, Any]) -> str:
        path = self._dir / f"{name}.py"
        if not path.exists():
            available = [s["name"] for s in self.list()]
            return f"Skill '{name}' not found. Available: {available}"

        spec = importlib.util.spec_from_file_location(f"shelby_skill_{name}", path)
        if spec is None or spec.loader is None:
            return f"Could not load skill '{name}'."

        module = importlib.util.module_from_spec(spec)
        # Give skills access to common stdlib + requests
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            return f"Skill '{name}' failed to load:\n{traceback.format_exc()}"

        if not hasattr(module, "run"):
            return f"Skill '{name}' has no run() function."

        try:
            result = module.run(**kwargs)
            return str(result)
        except Exception:
            return f"Skill '{name}' raised an error:\n{traceback.format_exc()}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_description(self, path: Path) -> str:
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("Description:"):
                    return line.split(":", 1)[1].strip()
            return "(no description)"
        except Exception:
            return "(unreadable)"
