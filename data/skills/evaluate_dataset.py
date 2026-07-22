"""Skill: evaluate_dataset
Description: Run the full inspect-fix-evaluate loop and return a before/after data-quality scorecard.
"""


def run(**kwargs) -> str:
    from shelby.dataquality.cleaner import clean_dataset
    from shelby.dataquality.evaluate import evaluate_quality
    from shelby.dataquality.generate_broken import generate

    gen = generate()
    cust, orders = gen["customers"], gen["orders"]

    before = evaluate_quality(cust, orders)
    result = clean_dataset(cust, orders)
    after = evaluate_quality(result["customers_clean"], result["orders_clean"])

    lines = ["📊 Data Quality Scorecard (before → after)", "─" * 44]
    lines.append(f"{'Dimension':<16}{'Before':>8}{'After':>8}")
    for dim in before["dimensions"]:
        b = before["dimensions"][dim]
        a = after["dimensions"][dim]
        lines.append(f"{dim:<16}{b:>8.1f}{a:>8.1f}")
    lines.append("─" * 44)
    lines.append(f"{'OVERALL':<16}{before['overall']:>8.1f}{after['overall']:>8.1f}")
    lines.append(f"\nImprovement: +{round(after['overall'] - before['overall'], 1)} points")
    if result["quarantined_rows"]:
        lines.append(f"{result['quarantined_rows']} unrecoverable rows quarantined for customer review.")
    return "\n".join(lines)
