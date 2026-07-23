"""Quick profile — generic data-quality scan for any uploaded/downloaded CSV.

Shared by the web UI's /inspect/upload endpoint and the Kaggle download tool,
so a file dropped into the browser and a file Shelby pulls from Kaggle get
identical treatment.
"""

from __future__ import annotations

import gzip
import io
import re

import pandas as pd

MAX_ROWS = 300_000  # keep large real-world exports (e.g. reviews.csv) fast + memory-safe
_CURRENCY_RE = re.compile(r"^\s*\$[\d,]+\.?\d*\s*$")
_PERCENT_RE = re.compile(r"^\s*\d+(\.\d+)?%\s*$")


def quick_profile_bytes(raw_bytes: bytes, name: str) -> dict:
    """Profile raw CSV (or gzip-compressed CSV) bytes. Returns a dict or {'error': ...}."""
    # Transparently handle gzip — Inside Airbnb and many real government
    # exports ship as .csv.gz.
    if raw_bytes[:2] == b"\x1f\x8b":
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except Exception as exc:
            return {"error": f"Could not decompress .gz file: {exc}"}

    try:
        df_iter = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=True,
                               chunksize=MAX_ROWS)
        df = next(df_iter)
        truncated = False
        try:
            next(df_iter)
            truncated = True
        except StopIteration:
            pass
    except Exception as exc:
        return {"error": f"Could not parse CSV: {exc}"}

    n = len(df)
    cols = list(df.columns)
    dupes = int(df.duplicated().sum())
    nulls = {c: int(df[c].isna().sum() + (df[c] == "").sum()) for c in cols}
    total_cells = n * len(cols) if cols else 0
    total_nulls = sum(nulls.values())

    # Format-level defects: currency strings, percent strings — the exact
    # issues real exports like Inside Airbnb's price/host_response_rate ship with.
    format_issues = []
    for col in cols:
        sample = df[col].dropna().astype(str).head(500)
        if sample.empty:
            continue
        if (sample.str.match(_CURRENCY_RE)).mean() > 0.5:
            format_issues.append(f"'{col}' stores currency as text (e.g. \"{sample.iloc[0]}\") — needs numeric coercion")
        elif (sample.str.match(_PERCENT_RE)).mean() > 0.5:
            format_issues.append(f"'{col}' stores percentages as text (e.g. \"{sample.iloc[0]}\") — needs numeric coercion")

    completeness = round(100 * (1 - total_nulls / total_cells), 1) if total_cells else 100.0
    uniqueness = round(100 * (1 - dupes / n), 1) if n else 100.0
    overall = round((completeness + uniqueness) / 2, 1)

    issues = list(format_issues)
    if dupes:
        issues.append(f"{dupes} duplicate rows")
    for col, cnt in sorted(nulls.items(), key=lambda x: -x[1])[:5]:
        if cnt:
            issues.append(f"{cnt} missing values in '{col}'")

    return {
        "filename": name,
        "rows": n,
        "truncated": truncated,
        "columns": cols,
        "duplicate_rows": dupes,
        "missing_cells": total_nulls,
        "scores": {"completeness": completeness, "uniqueness": uniqueness, "overall": overall},
        "issues": issues[:8],
    }
