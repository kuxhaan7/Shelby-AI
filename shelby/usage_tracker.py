"""Persist token usage to disk so Shelby can report stats across sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_FILE = Path("data/usage_stats.json")

# Approximate cost per million tokens (USD) — update if Anthropic pricing changes
_COST = {
    "claude-sonnet-5":              {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-6":            {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":    {"input": 0.80,  "output": 4.00},
}
_DEFAULT_COST = {"input": 3.00, "output": 15.00}


def record(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> None:
    data = _load()
    data["total_calls"] += 1
    data["total_input"]  += input_tokens
    data["total_output"] += output_tokens
    data["total_cache_read"]  += cache_read
    data["total_cache_write"] += cache_write
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    m = data["by_model"].setdefault(model, {"calls": 0, "input": 0, "output": 0})
    m["calls"]  += 1
    m["input"]  += input_tokens
    m["output"] += output_tokens

    _save(data)


def get_stats() -> dict:
    return _load()


def estimated_cost_usd(data: dict) -> float:
    total = 0.0
    for model, counts in data.get("by_model", {}).items():
        pricing = _COST.get(model, _DEFAULT_COST)
        total += counts["input"]  / 1_000_000 * pricing["input"]
        total += counts["output"] / 1_000_000 * pricing["output"]
    return round(total, 4)


def _load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text())
        except Exception:
            pass
    return {
        "total_calls": 0,
        "total_input": 0,
        "total_output": 0,
        "total_cache_read": 0,
        "total_cache_write": 0,
        "last_updated": None,
        "by_model": {},
    }


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, indent=2))
