"""Skill: inspect_dataset
Description: Inspect a broken enterprise dataset and diagnose all data-quality defects.
"""


def run(**kwargs) -> str:
    from shelby.dataquality.generate_broken import DATA_DIR, generate
    from shelby.dataquality.profiler import profile_dataset

    customers = kwargs.get("customers_path")
    orders = kwargs.get("orders_path")

    # If no paths given, generate the demo broken dataset
    if not customers or not orders:
        gen = generate()
        customers, orders = gen["customers"], gen["orders"]

    report = profile_dataset(customers, orders)
    lines = ["🔍 Data Quality Inspection", "─" * 40]
    for table, info in report["tables"].items():
        lines.append(f"\n{table}: {info['rows']} rows")
        for issue in info["issues"]:
            lines.append(f"  ✗ {issue}")
    for d in report["defects"]:
        lines.append(f"  ✗ {d}")
    return "\n".join(lines)
