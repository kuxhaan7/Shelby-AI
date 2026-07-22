"""End-to-end FDE loop demo: generate → inspect → fix → evaluate.

Run:  python -m shelby.dataquality.demo

Prints a before/after data-quality scorecard proving Shelby took a broken
enterprise dataset to a clean, joinable, production-ready state.
"""

from __future__ import annotations

from pathlib import Path

from .cleaner import clean_dataset
from .evaluate import evaluate_quality
from .generate_broken import DATA_DIR, generate
from .profiler import profile_dataset


def _bar(score: float, width: int = 24) -> str:
    filled = int(round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def run() -> dict:
    print("=" * 64)
    print("  SHELBY — Data Quality FDE Loop")
    print("  Scenario: two broken source-system exports that must join")
    print("=" * 64)

    # 1. Generate the broken dataset
    gen = generate()
    cust, orders = gen["customers"], gen["orders"]
    print(f"\n[1] Generated broken dataset")
    print(f"    customers.csv ({gen['customers_rows']} rows), orders.csv ({gen['orders_rows']} rows)")

    # 2. Inspect / diagnose
    print(f"\n[2] Inspecting — diagnosing defects…")
    report = profile_dataset(cust, orders)
    for table, info in report["tables"].items():
        print(f"\n    {table}: {info['rows']} rows")
        for issue in info["issues"]:
            print(f"      ✗ {issue}")
    for d in report["defects"]:
        print(f"      ✗ {d}")

    # 3. Score BEFORE
    before = evaluate_quality(cust, orders)

    # 4. Fix
    print(f"\n[3] Fixing — applying Foundry-style transformations…")
    result = clean_dataset(cust, orders)
    for line in result["changelog"]:
        print(f"      ✓ {line}")

    # 5. Score AFTER
    after = evaluate_quality(result["customers_clean"], result["orders_clean"])

    # 6. Scorecard
    print(f"\n[4] Evaluation — data-quality scorecard")
    print(f"\n    {'Dimension':<16}{'Before':>8}{'After':>8}   {'After bar'}")
    print(f"    {'-' * 55}")
    for dim in before["dimensions"]:
        b = before["dimensions"][dim]
        a = after["dimensions"][dim]
        print(f"    {dim:<16}{b:>7.1f}{a:>8.1f}   {_bar(a)}")
    print(f"    {'-' * 55}")
    print(f"    {'OVERALL':<16}{before['overall']:>7.1f}{after['overall']:>8.1f}   {_bar(after['overall'])}")

    print(f"\n    Result: {before['overall']} → {after['overall']}  "
          f"(+{round(after['overall'] - before['overall'], 1)} points)")
    if result["quarantined_rows"]:
        print(f"    {result['quarantined_rows']} unrecoverable rows quarantined for customer review.")
    print("=" * 64)

    return {"before": before, "after": after, "changelog": result["changelog"]}


if __name__ == "__main__":
    run()
