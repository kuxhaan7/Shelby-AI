"""Skill: fix_dataset
Description: Clean a real CSV file and return the transformation changelog. Pass path=<file>. Without a path it runs a clearly-labelled synthetic demo.
"""


def run(**kwargs) -> str:
    path = kwargs.get("path") or kwargs.get("file") or kwargs.get("csv_path")

    # ── Real file → clean the ACTUAL data, write cleaned copy ─────────────────
    if path:
        import os
        if not os.path.exists(path):
            return f"❌ File not found: {path}. Pass the exact path returned by kaggle_download."
        from shelby.dataquality.generic import clean_df, load_any
        try:
            df = load_any(path)
        except Exception as exc:
            return f"❌ Could not read {path}: {exc}"
        clean, changelog = clean_df(df)
        out_path = os.path.splitext(path)[0].replace(".csv", "") + "_clean.csv"
        try:
            clean.to_csv(out_path, index=False)
        except Exception:
            out_path = "(in-memory only)"
        lines = [
            f"🔧 REAL FILE CLEANED: {path}",
            f"{len(df):,} → {len(clean):,} rows after cleaning",
            "─" * 44,
        ]
        if changelog:
            for c in changelog:
                lines.append(f"  ✓ {c}")
        else:
            lines.append("  ✓ No transformations needed — file was already clean.")
        lines.append(f"\nClean output written to: {out_path}")
        return "\n".join(lines)

    # ── No path → synthetic demo, EXPLICITLY labelled ─────────────────────────
    from shelby.dataquality.cleaner import clean_dataset
    from shelby.dataquality.generate_broken import generate
    gen = generate()
    result = clean_dataset(gen["customers"], gen["orders"])
    lines = [
        "⚠️ SYNTHETIC DEMO DATASET (built-in customers×orders) — NOT a real/uploaded file.",
        "To clean real data, pass path=<file> (e.g. from kaggle_download).",
        "─" * 44,
    ]
    for line in result["changelog"]:
        lines.append(f"  ✓ {line}")
    if result["quarantined_rows"]:
        lines.append(f"Quarantined {result['quarantined_rows']} unrecoverable rows for review.")
    return "\n".join(lines)
