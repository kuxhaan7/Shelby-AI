"""Skill: evaluate_dataset
Description: Run the full inspect→fix→evaluate loop and return a before/after scorecard. Pass path=<file> for a REAL file; without a path it runs a clearly-labelled synthetic demo.
"""


def run(**kwargs) -> str:
    path = kwargs.get("path") or kwargs.get("file") or kwargs.get("csv_path")

    # ── Real file → honest before/after on the ACTUAL data ────────────────────
    if path:
        import os
        if not os.path.exists(path):
            return f"❌ File not found: {path}. Pass the exact path returned by kaggle_download."
        from shelby.dataquality.generic import run_loop
        try:
            r = run_loop(path)
        except Exception as exc:
            return f"❌ Could not process {path}: {exc}"
        lines = [
            f"📊 REAL FILE SCORECARD: {r['file']}",
            f"{r['rows']:,} rows × {len(r['columns'])} columns",
            "─" * 46,
            f"{'Dimension':<16}{'Before':>8}{'After':>8}",
        ]
        for dim in r["before"]["dimensions"]:
            b = r["before"]["dimensions"][dim]
            a = r["after"]["dimensions"][dim]
            lines.append(f"{dim:<16}{b:>8.1f}{a:>8.1f}")
        lines.append("─" * 46)
        lines.append(f"{'OVERALL':<16}{r['before']['overall']:>8.1f}{r['after']['overall']:>8.1f}")
        lines.append(f"\nDefects found: {len(r['defects'])} · Transformations applied: {len(r['changelog'])}")
        for c in r["changelog"][:6]:
            lines.append(f"  ✓ {c}")
        return "\n".join(lines)

    # ── No path → synthetic demo, EXPLICITLY labelled ─────────────────────────
    from shelby.dataquality.cleaner import clean_dataset
    from shelby.dataquality.evaluate import evaluate_quality
    from shelby.dataquality.generate_broken import generate
    gen = generate()
    cust, orders = gen["customers"], gen["orders"]
    before = evaluate_quality(cust, orders)
    result = clean_dataset(cust, orders)
    after = evaluate_quality(result["customers_clean"], result["orders_clean"])
    lines = [
        "⚠️ SYNTHETIC DEMO DATASET (built-in customers×orders) — NOT a real/uploaded file.",
        "To score real data, pass path=<file> (e.g. from kaggle_download).",
        "─" * 46,
        f"{'Dimension':<16}{'Before':>8}{'After':>8}",
    ]
    for dim in before["dimensions"]:
        lines.append(f"{dim:<16}{before['dimensions'][dim]:>8.1f}{after['dimensions'][dim]:>8.1f}")
    lines.append("─" * 46)
    lines.append(f"{'OVERALL':<16}{before['overall']:>8.1f}{after['overall']:>8.1f}")
    if result["quarantined_rows"]:
        lines.append(f"{result['quarantined_rows']} unrecoverable rows quarantined for review.")
    return "\n".join(lines)
