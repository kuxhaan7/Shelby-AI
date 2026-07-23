"""Generic data-quality loop for ANY real single CSV file.

Unlike the synthetic customers×orders demo (which is schema-specific), this
works on arbitrary real-world data — a Kaggle download, a government export,
an uploaded file. Every transformation is disclosed in a changelog, and the
before/after score reflects the ACTUAL file. Nothing is fabricated.

Public API:
    run_loop(path) -> dict   # load → profile → clean → evaluate, honest report
    profile_path(path) -> dict
"""

from __future__ import annotations

import gzip
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

MAX_ROWS = 300_000
_CURRENCY_RE = re.compile(r"^\s*[\$£€]\s?[\d,]+\.?\d*\s*$")
_PERCENT_RE = re.compile(r"^\s*\d+(\.\d+)?\s?%\s*$")
_COMMA_NUM_RE = re.compile(r"^\s*\d{1,3}(,\d{3})+(\.\d+)?\s*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Loading ───────────────────────────────────────────────────────────────────

def load_any(path: str | Path) -> pd.DataFrame:
    """Load a CSV, transparently handling .gz. Reads at most MAX_ROWS rows."""
    p = Path(path)
    raw = p.read_bytes()
    if raw[:2] == b"\x1f\x8b" or p.suffix == ".gz":
        raw = gzip.decompress(raw)
    import io
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=True, nrows=MAX_ROWS)


# ── Column-type detection ─────────────────────────────────────────────────────

def _frac_match(series: pd.Series, rx: re.Pattern) -> float:
    s = series.dropna().astype(str).head(1000)
    if s.empty:
        return 0.0
    return float(s.str.match(rx).mean())


def _detect_coercible(df: pd.DataFrame) -> dict[str, str]:
    """Return {column: kind} for columns storing numbers/dates as messy text."""
    kinds: dict[str, str] = {}
    for col in df.columns:
        s = df[col].dropna().astype(str)
        if s.empty:
            continue
        if _frac_match(df[col], _CURRENCY_RE) > 0.5:
            kinds[col] = "currency"
        elif _frac_match(df[col], _PERCENT_RE) > 0.5:
            kinds[col] = "percent"
        elif _frac_match(df[col], _COMMA_NUM_RE) > 0.5:
            kinds[col] = "comma_number"
        elif "date" in col.lower():
            # date-like column not already fully ISO
            iso_frac = _frac_match(df[col], _ISO_DATE_RE)
            if iso_frac < 0.99:
                parsed = pd.to_datetime(s.head(1000), errors="coerce", format="mixed")
                if parsed.notna().mean() > 0.7:
                    kinds[col] = "date"
    return kinds


def _casing_inconsistent_cols(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """For low-cardinality text columns, find values that collapse under case/space.

    Returns {column: {raw_value: canonical_value}} for columns that actually
    have inconsistent variants (so cleaning them is honest, not invented).
    """
    result: dict[str, dict[str, str]] = {}
    for col in df.columns:
        s = df[col].dropna().astype(str)
        if s.empty:
            continue
        nunique = s.nunique()
        if nunique == 0 or nunique > 60:
            continue  # skip free-text / high-cardinality columns
        groups: dict[str, list[str]] = defaultdict(list)
        for v in s.unique():
            groups[v.strip().lower()].append(v)
        mapping: dict[str, str] = {}
        has_variants = False
        for key, variants in groups.items():
            if len(variants) > 1:
                has_variants = True
            # canonical = most frequent original spelling
            canonical = s[s.isin(variants)].mode()
            canon = canonical.iloc[0] if not canonical.empty else variants[0]
            for v in variants:
                if v != canon:
                    mapping[v] = canon
        if has_variants and mapping:
            result[col] = mapping
    return result


# ── Coercion helpers ──────────────────────────────────────────────────────────

def _to_number(v: Any) -> Any:
    if pd.isna(v):
        return pd.NA
    s = re.sub(r"[^\d.\-]", "", str(v))
    if not s or s in {"-", "."}:
        return pd.NA
    try:
        return float(s)
    except ValueError:
        return pd.NA


def _to_iso(v: Any) -> Any:
    if pd.isna(v):
        return v
    dt = pd.to_datetime(str(v), errors="coerce", format="mixed")
    return dt.strftime("%Y-%m-%d") if pd.notna(dt) else v


# ── Profile ───────────────────────────────────────────────────────────────────

def profile_df(df: pd.DataFrame) -> dict:
    n = len(df)
    cols = list(df.columns)
    dupes = int(df.duplicated().sum())
    nulls = {c: int(df[c].isna().sum() + (df[c] == "").sum()) for c in cols}
    coercible = _detect_coercible(df)
    casing = _casing_inconsistent_cols(df)

    issues: list[str] = []
    for col, kind in coercible.items():
        example = df[col].dropna().astype(str)
        ex = example.iloc[0] if not example.empty else "?"
        label = {"currency": "currency", "percent": "percentages",
                 "comma_number": "comma-formatted numbers", "date": "non-ISO dates"}[kind]
        issues.append(f"'{col}' stores {label} as text (e.g. \"{ex}\") — needs coercion")
    for col in casing:
        sample = list(casing[col].items())[:2]
        pairs = ", ".join(f"'{k}'→'{v}'" for k, v in sample)
        issues.append(f"'{col}' has inconsistent categorical variants ({pairs})")
    if dupes:
        issues.append(f"{dupes} exact duplicate rows")
    for col, cnt in sorted(nulls.items(), key=lambda x: -x[1])[:5]:
        if cnt:
            issues.append(f"{cnt} missing values in '{col}'")

    return {
        "rows": n, "columns": cols, "duplicate_rows": dupes,
        "missing_cells": sum(nulls.values()),
        "coercible": coercible, "casing": casing, "issues": issues,
    }


# ── Evaluate ──────────────────────────────────────────────────────────────────

def evaluate_df(df: pd.DataFrame, coercible: dict | None = None, casing: dict | None = None) -> dict:
    n = len(df) or 1
    cols = list(df.columns)
    coercible = _detect_coercible(df) if coercible is None else coercible
    casing = _casing_inconsistent_cols(df) if casing is None else casing

    # completeness
    cells = n * len(cols) if cols else 1
    miss = sum(int(df[c].isna().sum() + (df[c].astype(str) == "").sum()) for c in cols)
    completeness = round(100 * (1 - miss / cells), 1)

    # uniqueness
    uniqueness = round(100 * (1 - int(df.duplicated().sum()) / n), 1)

    # validity: fraction of coercible cells already in clean numeric/ISO form
    if coercible:
        good = total = 0
        for col, kind in coercible.items():
            s = df[col].dropna()
            total += len(s)
            if kind == "date":
                good += int(s.astype(str).str.match(_ISO_DATE_RE).sum())
            else:
                good += int(pd.to_numeric(s, errors="coerce").notna().sum())
        validity = round(100 * good / total, 1) if total else 100.0
    else:
        validity = 100.0

    # consistency: fraction of the flagged categorical columns with no variants
    if casing:
        consistency = 0.0  # by definition these columns currently have variants
    else:
        consistency = 100.0

    overall = round((completeness + uniqueness + validity + consistency) / 4, 1)
    return {"dimensions": {
        "completeness": completeness, "uniqueness": uniqueness,
        "validity": validity, "consistency": consistency}, "overall": overall}


# ── Clean ─────────────────────────────────────────────────────────────────────

def clean_df(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    changelog: list[str] = []
    out = df.copy()
    coercible = _detect_coercible(out)
    casing = _casing_inconsistent_cols(out)

    # 1. trim whitespace on all text columns
    obj_cols = [c for c in out.columns]
    trimmed = 0
    for c in obj_cols:
        before = out[c].copy()
        out[c] = out[c].map(lambda v: v.strip() if isinstance(v, str) else v)
        trimmed += int((before != out[c]).sum())
    if trimmed:
        changelog.append(f"Trimmed whitespace from {trimmed} cells across all text columns.")

    # 2. dedupe
    d = int(out.duplicated().sum())
    if d:
        out = out.drop_duplicates().reset_index(drop=True)
        changelog.append(f"Dropped {d} exact duplicate rows.")

    # 3. coerce numeric/date columns
    for col, kind in coercible.items():
        if kind == "date":
            fixed = out[col].map(_to_iso)
            changed = int((fixed.astype(str) != out[col].astype(str)).sum())
            out[col] = fixed
            if changed:
                changelog.append(f"Standardised {changed} '{col}' values to ISO dates.")
        else:
            label = {"currency": "currency", "percent": "percent", "comma_number": "comma-number"}[kind]
            out[col] = out[col].map(_to_number)
            changelog.append(f"Coerced '{col}' from {label} text to numeric.")

    # 4. canonicalise categorical casing/spelling variants
    for col, mapping in casing.items():
        out[col] = out[col].map(lambda v: mapping.get(v, v) if isinstance(v, str) else v)
        changelog.append(f"Canonicalised {len(mapping)} inconsistent variant(s) in '{col}'.")

    # 5. impute remaining missing values (disclosed). Reassign the whole column
    #    to avoid dtype conflicts (e.g. float median into a str-dtype column).
    imputed = 0
    for col in out.columns:
        series = out[col]
        mask = series.isna() | (series.astype(str).isin(["", "nan", "<NA>", "None"]))
        cnt = int(mask.sum())
        if not cnt:
            continue
        num = pd.to_numeric(series, errors="coerce")
        if num.notna().mean() > 0.6:
            out[col] = num.fillna(num.median())
        else:
            out[col] = series.astype(object).where(~mask, "UNKNOWN")
        imputed += cnt
    if imputed:
        changelog.append(f"Imputed {imputed} missing values (numeric→median, text→'UNKNOWN') — disclosed, not silent.")

    return out, changelog


# ── Full loop ─────────────────────────────────────────────────────────────────

def run_loop(path: str | Path) -> dict:
    df = load_any(path)
    prof = profile_df(df)
    before = evaluate_df(df, prof["coercible"], prof["casing"])
    clean, changelog = clean_df(df)
    after = evaluate_df(clean)
    return {
        "file": str(path),
        "rows": prof["rows"],
        "columns": prof["columns"],
        "defects": prof["issues"],
        "changelog": changelog,
        "before": before,
        "after": after,
    }


def profile_path(path: str | Path) -> dict:
    df = load_any(path)
    prof = profile_df(df)
    prof["file"] = str(path)
    return prof
