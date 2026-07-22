"""Skill: get_usage_stats
Description: Show total token usage and estimated Anthropic API cost across all sessions.
"""


def run(**kwargs) -> str:
    import json
    from pathlib import Path

    file = Path("data/usage_stats.json")
    if not file.exists():
        return "No usage data recorded yet. Start chatting and check again."

    try:
        data = json.loads(file.read_text())
    except Exception as exc:
        return f"Could not read usage stats: {exc}"

    total_in  = data.get("total_input", 0)
    total_out = data.get("total_output", 0)
    total_cache_read  = data.get("total_cache_read", 0)
    total_cache_write = data.get("total_cache_write", 0)
    calls = data.get("total_calls", 0)
    updated = data.get("last_updated", "never")

    # Pricing per million tokens (USD)
    pricing = {
        "claude-sonnet-5":           {"input": 3.00,  "output": 15.00},
        "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
        "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    }
    default_price = {"input": 3.00, "output": 15.00}

    total_cost = 0.0
    model_lines = []
    for model, counts in data.get("by_model", {}).items():
        p = pricing.get(model, default_price)
        cost = counts["input"] / 1e6 * p["input"] + counts["output"] / 1e6 * p["output"]
        total_cost += cost
        model_lines.append(
            f"  • {model}: {counts['calls']} calls | "
            f"{counts['input']:,} in / {counts['output']:,} out | "
            f"~${cost:.4f}"
        )

    lines = [
        f"📊 Shelby Usage Stats",
        f"─────────────────────",
        f"Total chat calls : {calls:,}",
        f"Total input tokens : {total_in:,}",
        f"Total output tokens : {total_out:,}",
        f"Cache read tokens : {total_cache_read:,}",
        f"Cache write tokens : {total_cache_write:,}",
        f"",
        f"By model:",
    ] + model_lines + [
        f"",
        f"Estimated total cost : ~${total_cost:.4f} USD",
        f"Last updated : {updated}",
    ]

    return "\n".join(lines)
