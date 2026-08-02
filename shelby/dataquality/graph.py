"""The data-quality loop as a LangGraph state machine.

`generic.run_loop` runs inspect, repair, and score once, straight through. That
is fine when one cleaning pass is enough, but it cannot react to its own
result: if the repaired data still scores badly, it just reports the bad number.

This models the same work as a StateGraph so the pipeline can branch on what it
actually produced:

    load -> profile -> clean -> evaluate -> (score below target and attempts
    left?) -> quarantine -> clean (again, harder) -> evaluate -> ... -> finish

The branch that matters is not really "is the score high enough". A cleaner
that imputes aggressively can always reach a perfect score by inventing values,
and that score is a lie: the data looks complete because the gaps were filled
with placeholders, not because anything was recovered.

So the graph routes on trustworthiness, not just the number. The first pass
repairs conservatively and imputes. If too much of the result had to be
invented, the score is treated as inflated and the graph escalates: the invented
rows are quarantined to their own file for human review, and only the genuinely
recoverable data is re-cleaned and re-scored. What comes out the far side is a
score earned on real values.

Routing is deterministic (imputation rate, score threshold, attempt cap), so a
run costs no extra model calls.

The topology is introspectable, `describe_graph()` returns nodes and edges, and
`mermaid()` renders it, which is what the web UI draws.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph

from .generic import clean_df, evaluate_df, load_any, profile_df

# Stop retrying once the score clears this, or once we hit the attempt cap.
TARGET_SCORE = 95.0
MAX_ATTEMPTS = 3
# If more than this share of cells had to be invented, the score is inflated by
# imputation rather than earned, so escalate to quarantine instead of accepting it.
MAX_IMPUTED_SHARE = 0.10


class DQState(TypedDict, total=False):
    path: str
    df: pd.DataFrame
    profile: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    changelog: list[str]
    attempts: int
    strategy: str
    quarantined: int
    quarantine_file: str | None
    history: list[dict[str, Any]]
    imputed_share: float


# ── Nodes ────────────────────────────────────────────────────────────────────

def node_load(state: DQState) -> dict[str, Any]:
    df = load_any(state["path"])
    return {"df": df, "attempts": 0, "changelog": [], "history": [],
            "quarantined": 0, "quarantine_file": None, "strategy": "conservative"}


def node_profile(state: DQState) -> dict[str, Any]:
    df = state["df"]
    prof = profile_df(df)
    before = evaluate_df(df, prof["coercible"], prof["casing"])
    return {"profile": prof, "before": before}


def node_clean(state: DQState) -> dict[str, Any]:
    src = state["df"]
    # Measure how much of the result will be invented rather than recovered,
    # so the router can tell an earned score from an imputed one.
    total_cells = int(src.shape[0] * src.shape[1]) or 1
    missing = 0
    for col in src.columns:
        s = src[col]
        missing += int((s.isna() | s.astype(str).isin(["", "nan", "<NA>", "None"])).sum())
    imputed_share = missing / total_cells

    cleaned, log = clean_df(src)
    attempts = state.get("attempts", 0) + 1
    prefix = f"[pass {attempts}, {state.get('strategy', 'conservative')}] "
    return {
        "df": cleaned,
        "changelog": list(state.get("changelog", [])) + [prefix + line for line in log],
        "attempts": attempts,
        "imputed_share": imputed_share,
    }


def node_evaluate(state: DQState) -> dict[str, Any]:
    after = evaluate_df(state["df"])
    history = list(state.get("history", []))
    history.append({
        "attempt": state.get("attempts", 0),
        "strategy": state.get("strategy", "conservative"),
        "score": after["overall"],
        "imputed_share": round(state.get("imputed_share", 0.0), 4),
    })
    return {"after": after, "history": history}


def node_quarantine(state: DQState) -> dict[str, Any]:
    """Escalation step: isolate rows the conservative pass could not truly fix.

    A row is unrecoverable when the previous pass had to invent values for it
    (imputed placeholders). Those rows go to their own file for human review
    rather than silently polluting the cleaned output.
    """
    df = state["df"]
    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        col_str = df[col].astype(str)
        mask |= col_str.eq("UNKNOWN")

    bad = df[mask]
    kept = df[~mask].reset_index(drop=True)

    changelog = list(state.get("changelog", []))
    quarantine_file = state.get("quarantine_file")
    quarantined = int(state.get("quarantined", 0))

    if len(bad):
        from ..paths import outbox_dir
        stem = Path(state["path"]).stem
        out = outbox_dir() / f"{stem}_quarantined.csv"
        bad.to_csv(out, index=False)
        quarantine_file = str(out)
        quarantined += len(bad)
        changelog.append(
            f"[escalation] Quarantined {len(bad)} unrecoverable row(s) to "
            f"{out.name} for human review, rather than shipping invented values."
        )
    else:
        changelog.append("[escalation] No unrecoverable rows found to quarantine.")

    return {
        "df": kept if len(bad) else df,
        "changelog": changelog,
        "quarantined": quarantined,
        "quarantine_file": quarantine_file,
        "strategy": "aggressive",
    }


# ── Routing ──────────────────────────────────────────────────────────────────

def route_after_evaluate(state: DQState) -> str:
    """Deterministic. Escalate when the score is bad, or when it is only good
    because too much of the data was invented."""
    if state.get("attempts", 0) >= MAX_ATTEMPTS:
        return "done"
    # Only escalate once; a second quarantine pass has nothing left to isolate.
    if state.get("strategy") == "aggressive":
        return "done"
    score = state.get("after", {}).get("overall", 0.0)
    if score < TARGET_SCORE:
        return "escalate"
    if state.get("imputed_share", 0.0) > MAX_IMPUTED_SHARE:
        return "escalate"   # perfect score, but it was imputed, not earned
    return "done"


# ── Graph ────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(DQState)
    g.add_node("load", node_load)
    g.add_node("profile", node_profile)
    g.add_node("clean", node_clean)
    g.add_node("evaluate", node_evaluate)
    g.add_node("quarantine", node_quarantine)

    g.add_edge(START, "load")
    g.add_edge("load", "profile")
    g.add_edge("profile", "clean")
    g.add_edge("clean", "evaluate")
    g.add_conditional_edges(
        "evaluate", route_after_evaluate,
        {"done": END, "escalate": "quarantine"},
    )
    g.add_edge("quarantine", "clean")
    return g.compile()


_COMPILED = None


def _compiled():
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED


def run(path: str | Path) -> dict[str, Any]:
    """Run the graph over a real CSV and return a report."""
    final = _compiled().invoke({"path": str(path)})
    return {
        "file": str(path),
        "rows": final.get("profile", {}).get("rows"),
        "columns": final.get("profile", {}).get("columns"),
        "defects": final.get("profile", {}).get("issues", []),
        "changelog": final.get("changelog", []),
        "before": final.get("before"),
        "after": final.get("after"),
        "attempts": final.get("attempts"),
        "history": final.get("history", []),
        "quarantined": final.get("quarantined", 0),
        "quarantine_file": final.get("quarantine_file"),
        "target_score": TARGET_SCORE,
        "escalated": final.get("strategy") == "aggressive",
        "final_imputed_share": round(final.get("imputed_share", 0.0), 4),
    }


# ── Introspection (what the UI draws) ────────────────────────────────────────

def describe_graph() -> dict[str, Any]:
    graph = _compiled().get_graph()
    return {
        "nodes": [n.id for n in graph.nodes.values()],
        "edges": [
            {"source": e.source, "target": e.target,
             "conditional": bool(getattr(e, "conditional", False)),
             "label": getattr(e, "data", None)}
            for e in graph.edges
        ],
        "target_score": TARGET_SCORE,
        "max_attempts": MAX_ATTEMPTS,
    }


def mermaid() -> str:
    return _compiled().get_graph().draw_mermaid()
