"""Evaluate — prove the fix worked with an objective before/after quality score.

A data-quality score in [0, 100] across six dimensions an FDE is measured on:

  • completeness    — no missing cells in required columns
  • uniqueness      — no duplicate rows
  • validity        — dates parse, numbers are numeric
  • consistency     — categoricals share one canonical form
  • integrity       — every order joins to a real customer
  • key_hygiene     — join keys carry no whitespace/case noise

Each dimension contributes equally. The evaluation is deterministic so the demo
shows a stable 'before X → after 100' story.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

_KEY_NOISE = re.compile(r"^\s+|\s+$")
_CURRENCY = re.compile(r"[^\d.\-]")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DIMENSIONS = ["completeness", "uniqueness", "validity", "consistency", "integrity", "key_hygiene"]


def evaluate_quality(customers_path: str | Path, orders_path: str | Path) -> dict[str, Any]:
    """Score the two tables across six data-quality dimensions."""
    customers = pd.read_csv(customers_path, dtype=str, keep_default_na=True)
    orders = pd.read_csv(orders_path, dtype=str, keep_default_na=True)

    scores = {
        "completeness": _completeness(customers, orders),
        "uniqueness": _uniqueness(customers, orders),
        "validity": _validity(orders),
        "consistency": _consistency(customers, orders),
        "integrity": _integrity(customers, orders),
        "key_hygiene": _key_hygiene(customers, orders),
    }
    overall = round(sum(scores.values()) / len(scores), 1)
    return {"dimensions": scores, "overall": overall}


def quality_score(customers_path: str | Path, orders_path: str | Path) -> float:
    return evaluate_quality(customers_path, orders_path)["overall"]


# ── Dimension scorers (each returns 0–100) ────────────────────────────────────

def _completeness(customers: pd.DataFrame, orders: pd.DataFrame) -> float:
    required = {"customers": ["customer_id", "name"], "orders": ["order_id", "customer_id", "amount"]}
    total, missing = 0, 0
    for name, df in (("customers", customers), ("orders", orders)):
        for col in required[name]:
            if col in df.columns:
                total += len(df)
                missing += int(df[col].isna().sum() + (df[col] == "").sum())
    return round(100 * (1 - missing / total), 1) if total else 100.0


def _uniqueness(customers: pd.DataFrame, orders: pd.DataFrame) -> float:
    total = len(customers) + len(orders)
    dupes = int(customers.duplicated().sum() + orders.duplicated().sum())
    return round(100 * (1 - dupes / total), 1) if total else 100.0


def _validity(orders: pd.DataFrame) -> float:
    checks, passed = 0, 0
    # dates ISO
    if "order_date" in orders.columns:
        for v in orders["order_date"].dropna():
            checks += 1
            if _ISO.match(str(v).strip()):
                passed += 1
    # amounts numeric
    if "amount" in orders.columns:
        for v in orders["amount"].dropna():
            checks += 1
            s = str(v)
            if s.strip() == s and not _CURRENCY.search(s):
                try:
                    float(s)
                    passed += 1
                except ValueError:
                    pass
    return round(100 * passed / checks, 1) if checks else 100.0


def _consistency(customers: pd.DataFrame, orders: pd.DataFrame) -> float:
    from collections import defaultdict
    checks, clean = 0, 0
    for df, col in ((customers, "state"), (orders, "status")):
        if col in df.columns:
            groups: dict[str, set[str]] = defaultdict(set)
            for v in df[col].dropna():
                groups[str(v).strip().lower()].add(str(v))
            for vs in groups.values():
                checks += 1
                if len(vs) == 1:
                    clean += 1
    return round(100 * clean / checks, 1) if checks else 100.0


def _integrity(customers: pd.DataFrame, orders: pd.DataFrame) -> float:
    valid = {_norm(k) for k in customers["customer_id"].dropna()}
    keys = orders["customer_id"].dropna().map(_norm)
    if len(keys) == 0:
        return 100.0
    good = int(keys.isin(valid).sum())
    return round(100 * good / len(keys), 1)


def _key_hygiene(customers: pd.DataFrame, orders: pd.DataFrame) -> float:
    total, noisy = 0, 0
    for df in (customers, orders):
        for v in df["customer_id"].dropna():
            total += 1
            if _KEY_NOISE.search(str(v)) or str(v) != str(v).upper():
                noisy += 1
    return round(100 * (1 - noisy / total), 1) if total else 100.0


def _norm(key: Any) -> str:
    return _KEY_NOISE.sub("", str(key)).upper()


if __name__ == "__main__":
    import json
    from .generate_broken import DATA_DIR
    print(json.dumps(evaluate_quality(DATA_DIR / "customers.csv", DATA_DIR / "orders.csv"), indent=2))
