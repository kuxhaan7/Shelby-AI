"""Schema drift detection for the data-quality loop.

Remembers a lightweight fingerprint of a dataset's shape (its columns and a
simple inferred type per column) under a name, and reports what changed the
next time a file with that name is checked. This is what turns a one-off
"clean this file" into an ongoing pipeline: bind it to a webhook so a
recurring export is checked every time it lands, and Shelby flags a changed
schema before it silently breaks downstream, instead of after.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

MAX_ROWS = 300_000
_NUMERIC_RE = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")
_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?\s*$")


def _load(raw_bytes: bytes) -> pd.DataFrame:
    if raw_bytes[:2] == b"\x1f\x8b":
        raw_bytes = gzip.decompress(raw_bytes)
    df_iter = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=True, chunksize=MAX_ROWS)
    return next(df_iter)


def _infer_type(series: pd.Series) -> str:
    """A coarse, dependency-free type label: numeric, date, text, or empty."""
    sample = series.dropna().astype(str).head(200)
    if sample.empty:
        return "empty"
    if (sample.str.match(_NUMERIC_RE)).mean() > 0.8:
        return "numeric"
    if (sample.str.match(_DATE_RE)).mean() > 0.8:
        return "date"
    return "text"


def fingerprint(df: pd.DataFrame) -> dict[str, str]:
    """Column name -> inferred type, in column order."""
    return {col: _infer_type(df[col]) for col in df.columns}


def fingerprint_path(path: str | Path) -> dict[str, str]:
    raw = Path(path).read_bytes()
    return fingerprint(_load(raw))


# ── Persistence ──────────────────────────────────────────────────────────────

def _load_baselines() -> dict[str, dict[str, str]]:
    from ..paths import schema_baselines_file
    p = schema_baselines_file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_baselines(data: dict[str, dict[str, str]]) -> None:
    from ..paths import schema_baselines_file
    p = schema_baselines_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def check(name: str, path: str | Path) -> dict[str, Any]:
    """Compare *path*'s schema against the saved baseline for *name*.

    The first call for a given name saves the current schema as the baseline
    and reports no drift. Every later call diffs against it: columns added,
    columns removed, and columns whose inferred type changed.
    """
    name = (name or "").strip() or Path(path).stem
    current = fingerprint_path(path)
    baselines = _load_baselines()
    baseline = baselines.get(name)

    if baseline is None:
        baselines[name] = current
        _save_baselines(baselines)
        return {
            "name": name, "first_run": True, "drifted": False,
            "added": [], "removed": [], "type_changed": [],
            "columns": list(current.keys()),
        }

    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    type_changed = [
        {"column": c, "was": baseline[c], "now": current[c]}
        for c in sorted(set(current) & set(baseline))
        if baseline[c] != current[c]
    ]
    drifted = bool(added or removed or type_changed)
    return {
        "name": name, "first_run": False, "drifted": drifted,
        "added": added, "removed": removed, "type_changed": type_changed,
        "columns": list(current.keys()),
    }


def update_baseline(name: str, path: str | Path) -> dict[str, Any]:
    """Explicitly reset the baseline for *name* to *path*'s current schema."""
    name = (name or "").strip() or Path(path).stem
    current = fingerprint_path(path)
    baselines = _load_baselines()
    baselines[name] = current
    _save_baselines(baselines)
    return {"name": name, "columns": list(current.keys())}


def list_baselines() -> list[dict[str, Any]]:
    baselines = _load_baselines()
    return [{"name": n, "columns": list(fp.keys())} for n, fp in sorted(baselines.items())]


def remove_baseline(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    baselines = _load_baselines()
    if name not in baselines:
        return {"ok": False, "error": f"No baseline named '{name}'."}
    del baselines[name]
    _save_baselines(baselines)
    return {"ok": True, "name": name}
