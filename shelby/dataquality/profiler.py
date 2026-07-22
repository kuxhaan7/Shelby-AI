"""Profiler — inspect a dataset and diagnose data-quality defects.

Returns a structured report an FDE would hand a customer: what's broken, where,
and how bad. Deliberately dependency-light so it can run as a Shelby skill.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

_KEY_NOISE = re.compile(r"^\s+|\s+$")
_CURRENCY = re.compile(r"[^\d.\-]")


def profile_dataset(
    customers_path: str | Path,
    orders_path: str | Path,
) -> dict[str, Any]:
    """Inspect the two source tables and return a defect report."""
    customers = pd.read_csv(customers_path, dtype=str, keep_default_na=True)
    orders = pd.read_csv(orders_path, dtype=str, keep_default_na=True)

    report: dict[str, Any] = {"tables": {}, "defects": [], "join": {}}

    for name, df in (("customers", customers), ("orders", orders)):
        t = _profile_table(name, df)
        report["tables"][name] = t

    # ── Referential integrity: orders → customers ─────────────────────────────
    cust_keys = {_norm_key(k) for k in customers["customer_id"].dropna()}
    order_keys = orders["customer_id"].dropna().map(_norm_key)
    orphans = [k for k in order_keys if k not in cust_keys]
    orphan_count = len(orphans)
    total_orders = len(orders)

    report["join"] = {
        "orders_total": total_orders,
        "orphaned_orders": orphan_count,
        "orphan_pct": round(100 * orphan_count / total_orders, 2) if total_orders else 0.0,
        "example_orphans": sorted(set(orphans))[:5],
    }
    if orphan_count:
        report["defects"].append(
            f"Broken referential integrity: {orphan_count} orders "
            f"({report['join']['orphan_pct']}%) reference customers that don't exist."
        )

    # ── Key-noise detection (whitespace / case in join keys) ──────────────────
    raw_keys = customers["customer_id"].dropna().tolist()
    noisy = [k for k in raw_keys if _norm_key(k) != k]
    if noisy:
        report["defects"].append(
            f"Join-key noise: {len(noisy)} customer_id values have whitespace/case "
            f"issues (e.g. {noisy[:3]}) that would silently break joins."
        )

    return report


def _profile_table(name: str, df: pd.DataFrame) -> dict[str, Any]:
    n = len(df)
    dupes = int(df.duplicated().sum())
    nulls = {c: int(df[c].isna().sum() + (df[c] == "").sum()) for c in df.columns}
    total_nulls = sum(nulls.values())

    result: dict[str, Any] = {
        "rows": n,
        "columns": list(df.columns),
        "duplicate_rows": dupes,
        "null_counts": nulls,
        "total_missing_cells": total_nulls,
        "issues": [],
    }

    if dupes:
        result["issues"].append(f"{dupes} exact duplicate rows")
    for col, cnt in nulls.items():
        if cnt:
            result["issues"].append(f"{cnt} missing values in '{col}'")

    # Date-format inconsistency
    for col in df.columns:
        if "date" in col.lower():
            formats = _detect_date_formats(df[col].dropna())
            if len(formats) > 1:
                result["issues"].append(
                    f"'{col}' has {len(formats)} inconsistent date formats: {sorted(formats)}"
                )

    # Categorical casing inconsistency (state, status)
    for col in ("state", "status"):
        if col in df.columns:
            variants = _casing_variants(df[col].dropna())
            if variants:
                result["issues"].append(
                    f"'{col}' has casing/spelling variants of the same value: {variants[:4]}"
                )

    # Numeric columns stored as messy strings
    for col in ("amount",):
        if col in df.columns:
            messy = _count_messy_numeric(df[col].dropna())
            if messy:
                result["issues"].append(
                    f"'{col}' has {messy} values with non-numeric formatting ($ , spaces)"
                )

    return result


def _norm_key(key: str) -> str:
    return _KEY_NOISE.sub("", str(key)).upper()


def _detect_date_formats(series: pd.Series) -> set[str]:
    formats = set()
    for v in series:
        v = str(v).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            formats.add("ISO (YYYY-MM-DD)")
        elif re.match(r"^\d{2}/\d{2}/\d{4}$", v):
            formats.add("US (MM/DD/YYYY)")
        elif re.match(r"^\d{1,2} [A-Za-z]{3} \d{4}$", v):
            formats.add("Text (DD Mon YYYY)")
        else:
            formats.add("Unknown")
    return formats


def _casing_variants(series: pd.Series) -> list[str]:
    """Return values that collapse to the same thing when normalised."""
    from collections import defaultdict
    groups: dict[str, set[str]] = defaultdict(set)
    for v in series:
        groups[str(v).strip().lower()].add(str(v))
    return [f"{sorted(vs)}" for vs in groups.values() if len(vs) > 1]


def _count_messy_numeric(series: pd.Series) -> int:
    count = 0
    for v in series:
        s = str(v)
        if s.strip() != s or _CURRENCY.search(s):
            count += 1
    return count


if __name__ == "__main__":
    import json
    from .generate_broken import DATA_DIR
    rep = profile_dataset(DATA_DIR / "customers.csv", DATA_DIR / "orders.csv")
    print(json.dumps(rep, indent=2))
