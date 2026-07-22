"""Generate a realistically-broken enterprise dataset.

This mirrors the canonical Palantir Foundry integration scenario: two source
systems (a CRM 'customers' export and an ERP 'orders' export) that are supposed
to join on customer_id but arrive full of the defects real source systems ship:

  1. Duplicate rows (double-synced records)
  2. Missing values (nulls in required fields)
  3. Inconsistent date formats (US vs ISO vs text months)
  4. Inconsistent categorical casing / typos ('CA', 'Ca', 'california')
  5. Broken referential integrity (orders pointing at customers that don't exist)
  6. Mixed units / bad numeric formatting ('$1,200.00', '1200', ' 1200 ')
  7. Whitespace and case noise in join keys ('C001', ' c001 ', 'C001 ')

The generator is deterministic (fixed seed) so the demo is reproducible and the
before/after evaluation is stable.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

SEED = 42
DATA_DIR = Path("data/dataquality")


def _states_dirty() -> list[str]:
    # Same logical value expressed inconsistently — the classic source-system mess
    return ["CA", "Ca", "california", "CALIFORNIA", "NY", "ny", "New York", "TX", "tx", "Texas"]


def generate(out_dir: Path | None = None) -> dict[str, str]:
    """Write broken customers.csv and orders.csv. Returns paths + defect summary."""
    random.seed(SEED)
    out_dir = out_dir or DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Customers (CRM export) ────────────────────────────────────────────────
    customers = []
    for i in range(1, 201):
        cid = f"C{i:03d}"
        customers.append({
            "customer_id": cid,
            "name": f"Customer {i}",
            "state": random.choice(_states_dirty()),
            "signup_date": _dirty_date(2020 + i % 5, (i % 12) + 1, (i % 28) + 1),
            "email": f"customer{i}@example.com" if i % 13 != 0 else None,  # some nulls
        })

    # Inject duplicate rows (double-synced)
    for dup_idx in (5, 42, 99, 150):
        customers.append(dict(customers[dup_idx]))

    # Inject whitespace/case noise into some join keys
    customers[10]["customer_id"] = " C011 "
    customers[20]["customer_id"] = "c021"
    customers[30]["customer_id"] = "C031 "

    # Inject missing required field (name)
    customers[7]["name"] = None
    customers[88]["name"] = ""

    df_customers = pd.DataFrame(customers)

    # ── Orders (ERP export) ───────────────────────────────────────────────────
    orders = []
    for j in range(1, 501):
        # Most orders reference a real customer; some reference ghosts
        if j % 17 == 0:
            cid = f"C{random.randint(900, 999):03d}"  # broken referential integrity
        else:
            cid = f"C{random.randint(1, 200):03d}"
        orders.append({
            "order_id": f"O{j:04d}",
            "customer_id": cid,
            "amount": _dirty_amount(random.uniform(10, 5000)),
            "order_date": _dirty_date(2023 + j % 3, (j % 12) + 1, (j % 28) + 1),
            "status": random.choice(["shipped", "Shipped", "SHIPPED", "pending", "Pending", "cancelled"]),
        })

    # Duplicate order rows
    for dup_idx in (3, 77, 200, 333, 400):
        orders.append(dict(orders[dup_idx]))

    # Null amounts
    orders[15]["amount"] = None
    orders[250]["amount"] = ""

    df_orders = pd.DataFrame(orders)

    cust_path = out_dir / "customers.csv"
    ord_path = out_dir / "orders.csv"
    df_customers.to_csv(cust_path, index=False)
    df_orders.to_csv(ord_path, index=False)

    return {
        "customers": str(cust_path),
        "orders": str(ord_path),
        "customers_rows": str(len(df_customers)),
        "orders_rows": str(len(df_orders)),
    }


def _dirty_date(year: int, month: int, day: int) -> str:
    """Return the same date in a randomly-chosen inconsistent format."""
    fmt = random.choice(["iso", "us", "text"])
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if fmt == "iso":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if fmt == "us":
        return f"{month:02d}/{day:02d}/{year:04d}"
    return f"{day} {months[month - 1]} {year}"


def _dirty_amount(value: float) -> str:
    """Return a numeric amount in a randomly-chosen messy string format."""
    fmt = random.choice(["plain", "currency", "spaced"])
    if fmt == "plain":
        return f"{value:.2f}"
    if fmt == "currency":
        return f"${value:,.2f}"
    return f"  {value:.2f} "


if __name__ == "__main__":
    result = generate()
    print("Generated broken dataset:")
    for k, v in result.items():
        print(f"  {k}: {v}")
