"""Skill: fix_dataset
Description: Clean a broken enterprise dataset and return the transformation changelog.
"""


def run(**kwargs) -> str:
    from shelby.dataquality.cleaner import clean_dataset
    from shelby.dataquality.generate_broken import generate

    customers = kwargs.get("customers_path")
    orders = kwargs.get("orders_path")

    if not customers or not orders:
        gen = generate()
        customers, orders = gen["customers"], gen["orders"]

    result = clean_dataset(customers, orders)
    lines = ["🔧 Data Quality Fix — Changelog", "─" * 40]
    for line in result["changelog"]:
        lines.append(f"  ✓ {line}")
    lines.append(f"\nClean output: {result['customers_clean']}, {result['orders_clean']}")
    if result["quarantined_rows"]:
        lines.append(f"Quarantined {result['quarantined_rows']} unrecoverable rows for review.")
    return "\n".join(lines)
