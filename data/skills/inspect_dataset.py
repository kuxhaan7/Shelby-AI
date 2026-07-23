"""Skill: inspect_dataset
Description: Inspect a real CSV file for data-quality defects. Pass path=<file>. Without a path it runs a clearly-labelled synthetic demo.
"""


def run(**kwargs) -> str:
    path = kwargs.get("path") or kwargs.get("file") or kwargs.get("csv_path")

    # ── Real file path given → analyse the ACTUAL file ────────────────────────
    if path:
        import os
        if not os.path.exists(path):
            return f"❌ File not found: {path}. Pass the exact path returned by kaggle_download."
        from shelby.dataquality.generic import profile_path
        try:
            p = profile_path(path)
        except Exception as exc:
            return f"❌ Could not read {path}: {exc}"
        lines = [
            f"🔍 REAL FILE INSPECTED: {p['file']}",
            f"{p['rows']:,} rows × {len(p['columns'])} columns",
            "─" * 44,
        ]
        if p["issues"]:
            for issue in p["issues"]:
                lines.append(f"  ✗ {issue}")
        else:
            lines.append("  ✓ No obvious defects detected.")
        return "\n".join(lines)

    # ── No path → synthetic demo, EXPLICITLY labelled (never passed off as real)
    from shelby.dataquality.generate_broken import generate
    from shelby.dataquality.profiler import profile_dataset
    gen = generate()
    report = profile_dataset(gen["customers"], gen["orders"])
    lines = [
        "⚠️ SYNTHETIC DEMO DATASET (built-in customers×orders) — this is NOT a real/uploaded file.",
        "To inspect real data, pass path=<file> (e.g. from kaggle_download).",
        "─" * 44,
    ]
    for table, info in report["tables"].items():
        lines.append(f"\n{table}: {info['rows']} rows")
        for issue in info["issues"]:
            lines.append(f"  ✗ {issue}")
    for d in report["defects"]:
        lines.append(f"  ✗ {d}")
    return "\n".join(lines)
