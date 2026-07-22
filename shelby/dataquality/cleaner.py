"""Cleaner — the fix half of the FDE loop.

Applies the transformations a Foundry pipeline would: normalise join keys,
dedupe, standardise dates and categoricals, coerce numerics, drop or quarantine
rows that can't be salvaged (orphaned orders, unrecoverable nulls).

Returns the cleaned frames plus a changelog of exactly what was done — because
an FDE has to be able to explain every transformation to the customer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

_KEY_NOISE = re.compile(r"^\s+|\s+$")
_CURRENCY = re.compile(r"[^\d.\-]")

_STATE_CANON = {
    "ca": "CA", "california": "CA",
    "ny": "NY", "new york": "NY",
    "tx": "TX", "texas": "TX",
}


def clean_dataset(
    customers_path: str | Path,
    orders_path: str | Path,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Clean both tables. Writes cleaned CSVs and returns a changelog."""
    customers = pd.read_csv(customers_path, dtype=str, keep_default_na=True)
    orders = pd.read_csv(orders_path, dtype=str, keep_default_na=True)
    changelog: list[str] = []

    # ── 1. Normalise join keys ────────────────────────────────────────────────
    before_noisy = (customers["customer_id"].map(_norm_key) != customers["customer_id"]).sum()
    customers["customer_id"] = customers["customer_id"].map(_norm_key)
    orders["customer_id"] = orders["customer_id"].map(_norm_key)
    if before_noisy:
        changelog.append(f"Normalised {int(before_noisy)} noisy customer_id join keys (trim + uppercase).")

    # ── 2. Deduplicate ────────────────────────────────────────────────────────
    c_dupes = int(customers.duplicated().sum())
    o_dupes = int(orders.duplicated().sum())
    customers = customers.drop_duplicates().reset_index(drop=True)
    orders = orders.drop_duplicates().reset_index(drop=True)
    if c_dupes:
        changelog.append(f"Dropped {c_dupes} duplicate customer rows.")
    if o_dupes:
        changelog.append(f"Dropped {o_dupes} duplicate order rows.")

    # ── 3. Standardise dates → ISO ────────────────────────────────────────────
    for df, col in ((customers, "signup_date"), (orders, "order_date")):
        if col in df.columns:
            fixed = df[col].map(_to_iso)
            changed = int((fixed != df[col]).sum())
            df[col] = fixed
            if changed:
                changelog.append(f"Standardised {changed} '{col}' values to ISO (YYYY-MM-DD).")

    # ── 4. Standardise categoricals ───────────────────────────────────────────
    if "state" in customers.columns:
        fixed = customers["state"].map(_canon_state)
        changed = int((fixed != customers["state"]).sum())
        customers["state"] = fixed
        if changed:
            changelog.append(f"Canonicalised {changed} 'state' values (e.g. 'california' → 'CA').")

    if "status" in orders.columns:
        fixed = orders["status"].map(lambda s: str(s).strip().lower() if pd.notna(s) else s)
        changed = int((fixed != orders["status"]).sum())
        orders["status"] = fixed
        if changed:
            changelog.append(f"Lower-cased {changed} 'status' values for consistency.")

    # ── 5. Coerce numeric amounts ─────────────────────────────────────────────
    if "amount" in orders.columns:
        orders["amount"] = orders["amount"].map(_to_number)
        n_null_amt = int(orders["amount"].isna().sum())
        # Fill unrecoverable amounts with 0.0 and flag
        if n_null_amt:
            orders["amount"] = orders["amount"].fillna(0.0)
            changelog.append(f"Coerced 'amount' to numeric; set {n_null_amt} unparseable/blank amounts to 0.0.")
        changelog.append("Stripped currency symbols/whitespace from 'amount' → float.")

    # ── 6. Handle missing required fields ─────────────────────────────────────
    if "name" in customers.columns:
        blank_names = int(customers["name"].isna().sum() + (customers["name"] == "").sum())
        customers["name"] = customers["name"].replace("", pd.NA)
        customers["name"] = customers["name"].fillna("UNKNOWN")
        if blank_names:
            changelog.append(f"Filled {blank_names} missing customer names with 'UNKNOWN'.")

    # ── 7. Quarantine orphaned orders (broken referential integrity) ──────────
    valid_ids = set(customers["customer_id"].dropna())
    is_orphan = ~orders["customer_id"].isin(valid_ids)
    orphan_count = int(is_orphan.sum())
    quarantine = orders[is_orphan].reset_index(drop=True)
    orders = orders[~is_orphan].reset_index(drop=True)
    if orphan_count:
        changelog.append(
            f"Quarantined {orphan_count} orphaned orders (customer_id not in customers) "
            f"into orders_quarantine.csv rather than silently dropping them."
        )

    # ── Write outputs ─────────────────────────────────────────────────────────
    out_dir = Path(out_dir) if out_dir else Path(customers_path).parent / "clean"
    out_dir.mkdir(parents=True, exist_ok=True)
    customers.to_csv(out_dir / "customers_clean.csv", index=False)
    orders.to_csv(out_dir / "orders_clean.csv", index=False)
    if orphan_count:
        quarantine.to_csv(out_dir / "orders_quarantine.csv", index=False)

    return {
        "changelog": changelog,
        "customers_clean": str(out_dir / "customers_clean.csv"),
        "orders_clean": str(out_dir / "orders_clean.csv"),
        "quarantine": str(out_dir / "orders_quarantine.csv") if orphan_count else None,
        "customers_rows": len(customers),
        "orders_rows": len(orders),
        "quarantined_rows": orphan_count,
    }


def _norm_key(key: Any) -> Any:
    if pd.isna(key):
        return key
    return _KEY_NOISE.sub("", str(key)).upper()


def _canon_state(v: Any) -> Any:
    if pd.isna(v):
        return v
    return _STATE_CANON.get(str(v).strip().lower(), str(v).strip().upper())


def _to_iso(v: Any) -> Any:
    if pd.isna(v):
        return v
    s = str(v).strip()
    months = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)  # MM/DD/YYYY
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    m = re.match(r"^(\d{1,2}) ([A-Za-z]{3}) (\d{4})$", s)  # DD Mon YYYY
    if m and m.group(2) in months:
        return f"{m.group(3)}-{months[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return s


def _to_number(v: Any) -> Any:
    if pd.isna(v):
        return pd.NA
    s = _CURRENCY.sub("", str(v))
    if not s or s in {"-", "."}:
        return pd.NA
    try:
        return float(s)
    except ValueError:
        return pd.NA


if __name__ == "__main__":
    from .generate_broken import DATA_DIR
    result = clean_dataset(DATA_DIR / "customers.csv", DATA_DIR / "orders.csv")
    print("Cleaning changelog:")
    for line in result["changelog"]:
        print(f"  • {line}")
