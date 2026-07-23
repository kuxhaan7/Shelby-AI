"""Run Shelby's eval suite as a LangSmith experiment.

Uploads the eval dataset to LangSmith, has Shelby answer each question
(grounded strictly in the provided context), and scores every answer with the
LLM-as-judge evaluators. Results appear as a scored **Experiment** in the
LangSmith UI, and every agent + judge call is traced.

Setup (never commit these — they live in the shell / Railway variables):
    export LANGSMITH_TRACING=true
    export LANGSMITH_API_KEY=<your langsmith key>
    export LANGSMITH_PROJECT=Shelby            # optional; tracing project
    export ANTHROPIC_API_KEY=<your anthropic key>

Run:
    python -m shelby.evals.langsmith_run
    python -m shelby.evals.langsmith_run --repetitions 3   # variance check

A run "passes" when every example clears the thresholds (faithfulness and
relevance ≥ 0.7 by default; override with SHELBY_EVAL_PASS_THRESHOLD).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import EVAL_DATASET

# Repo root (…/Shelby-AI): shelby/evals/langsmith_run.py → parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = _REPO_ROOT / "evals_results"
RESULTS_MD = _REPO_ROOT / "docs" / "EVAL_RESULTS.md"
from .evaluators import (
    evaluate_answer_relevance,
    evaluate_conciseness,
    evaluate_faithfulness,
)

DATASET_NAME = os.getenv("LANGSMITH_DATASET", "shelby-qa")
PASS_THRESHOLD = float(os.getenv("SHELBY_EVAL_PASS_THRESHOLD", "0.7"))

_GROUNDED = (
    "Answer the question using ONLY the information in the context below. "
    "Do not add facts, tools, or details not stated there; if the context "
    "lists specific items, do not go beyond them.\n\n"
    "Context:\n{context}\n\nQuestion: {question}"
)

# Built once, reused across every example so RAG/model setup isn't repeated.
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from ..agent import ShelbyAgent
        from ..rag.store import RagStore

        agent = ShelbyAgent(rag_store=RagStore())
        # Wrap the raw Anthropic client so agent LLM calls show up as nested
        # traces in LangSmith (only when tracing is actually enabled).
        if os.getenv("LANGSMITH_TRACING", "").lower() in ("1", "true", "yes"):
            try:
                from langsmith.wrappers import wrap_anthropic

                agent._client = wrap_anthropic(agent._client)
            except Exception:
                pass
        _agent = agent
    return _agent


def shelby_target(inputs: dict) -> dict:
    """The system under test: Shelby answering a context-grounded question."""
    prompt = _GROUNDED.format(context=inputs["context"], question=inputs["question"])
    answer = _get_agent().chat([{"role": "user", "content": prompt}])
    return {"answer": answer}


# ── Evaluators (LangSmith run/example signature) ─────────────────────────────

def _faithfulness(run, example) -> dict[str, Any]:
    r = evaluate_faithfulness(run.outputs["answer"], example.inputs["context"])
    return {"key": "faithfulness", "score": r["score"], "comment": r["reasoning"]}


def _relevance(run, example) -> dict[str, Any]:
    r = evaluate_answer_relevance(
        example.inputs["question"], run.outputs["answer"], example.inputs["context"]
    )
    return {"key": "relevance", "score": r["score"], "comment": r["reasoning"]}


def _conciseness(run, example) -> dict[str, Any]:
    r = evaluate_conciseness(example.inputs["question"], run.outputs["answer"])
    return {"key": "conciseness", "score": r["score"], "comment": r["reasoning"]}


EVALUATORS = [_faithfulness, _relevance, _conciseness]


# ── Dataset upload ───────────────────────────────────────────────────────────

def ensure_dataset(client) -> Any:
    """Create the LangSmith dataset from EVAL_DATASET if it doesn't exist."""
    if client.has_dataset(dataset_name=DATASET_NAME):
        return client.read_dataset(dataset_name=DATASET_NAME)
    ds = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Shelby context-grounded QA — faithfulness / relevance / conciseness.",
    )
    client.create_examples(
        dataset_id=ds.id,
        inputs=[{"question": s["question"], "context": s["context"]} for s in EVAL_DATASET],
        outputs=[{"ground_truth": s["ground_truth"]} for s in EVAL_DATASET],
    )
    return ds


# ── Runner ───────────────────────────────────────────────────────────────────

def run(repetitions: int = 1, save: bool = True) -> dict[str, Any]:
    for var in ("LANGSMITH_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.getenv(var):
            print(f"{var} not set — cannot run LangSmith evals.", file=sys.stderr)
            sys.exit(1)

    from langsmith import Client, evaluate

    client = Client()
    ensure_dataset(client)

    results = evaluate(
        shelby_target,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        experiment_prefix="shelby-qa",
        num_repetitions=repetitions,
        metadata={"suite": "context-grounded-qa", "threshold": PASS_THRESHOLD},
        client=client,
    )

    experiment = getattr(results, "experiment_name", None)
    outcome = _summarise(results, experiment=experiment)
    if save:
        _save_results(outcome)
    return outcome


def _summarise(results, experiment: str | None = None) -> dict[str, Any]:
    """Aggregate per-metric averages and decide pass/fail against the threshold."""
    rows = list(results)
    scores: dict[str, list[float]] = {"faithfulness": [], "relevance": [], "conciseness": []}
    failures: list[str] = []
    examples: list[dict[str, Any]] = []

    for r in rows:
        ex = r["example"]
        q = ex.inputs.get("question", "?")
        per: dict[str, Any] = {}
        for res in r["evaluation_results"]["results"]:
            if res.score is not None:
                scores.setdefault(res.key, []).append(res.score)
                per[res.key] = res.score
        examples.append({"question": q, "scores": per})
        # Gate on faithfulness + relevance (conciseness is informational)
        for gate in ("faithfulness", "relevance"):
            if per.get(gate, 0.0) < PASS_THRESHOLD:
                failures.append(f"{q[:48]} — {gate}={per.get(gate)}")

    avgs = {k: (sum(v) / len(v) if v else None) for k, v in scores.items()}
    passed = not failures

    print("\n" + "=" * 60)
    print(f"LangSmith experiment complete · dataset='{DATASET_NAME}'")
    for k, v in avgs.items():
        print(f"  avg {k:<13} {v:.2f}" if v is not None else f"  avg {k:<13} N/A")
    print(f"  gate: faithfulness & relevance ≥ {PASS_THRESHOLD}")
    print(f"  RESULT: {'✅ PASSED' if passed else '❌ FAILED'}")
    for f in failures:
        print(f"    ✗ {f}")
    print("=" * 60)

    return {
        "passed": passed,
        "averages": avgs,
        "failures": failures,
        "n": len(rows),
        "examples": examples,
        "experiment": experiment,
        "dataset": DATASET_NAME,
        "threshold": PASS_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _save_results(outcome: dict[str, Any]) -> None:
    """Persist a run to git-tracked files: a JSON record + a Markdown summary."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)

    stamp = outcome["timestamp"].replace(":", "").replace("-", "")
    json_path = RESULTS_DIR / f"{stamp}_{outcome.get('experiment') or 'shelby-qa'}.json"
    json_path.write_text(json.dumps(outcome, indent=2))

    def _fmt(v: Any) -> str:
        return f"{v:.2f}" if isinstance(v, (int, float)) else "N/A"

    lines = [
        "# Shelby — LangChain / LangSmith eval results",
        "",
        "_Auto-written by `python -m shelby.evals.langsmith_run`. Latest run at top._",
        "",
        f"## {outcome['timestamp']} — {'✅ PASSED' if outcome['passed'] else '❌ FAILED'}",
        "",
        f"- Dataset: `{outcome['dataset']}`"
        + (f" · experiment: `{outcome['experiment']}`" if outcome.get("experiment") else ""),
        f"- Gate: faithfulness & relevance ≥ {outcome['threshold']}",
        f"- Averages: "
        + ", ".join(f"{k} {_fmt(v)}" for k, v in outcome["averages"].items()),
        "",
        "| Question | Faithfulness | Relevance | Conciseness |",
        "|----------|:------------:|:---------:|:-----------:|",
    ]
    for ex in outcome["examples"]:
        s = ex["scores"]
        lines.append(
            f"| {ex['question'][:52]} | {_fmt(s.get('faithfulness'))} "
            f"| {_fmt(s.get('relevance'))} | {_fmt(s.get('conciseness'))} |"
        )
    lines.append("")
    RESULTS_MD.write_text("\n".join(lines))

    print(f"\nSaved results → {json_path.relative_to(_REPO_ROOT)}")
    print(f"Saved summary → {RESULTS_MD.relative_to(_REPO_ROOT)}")
    print("Commit them with:  git add evals_results docs/EVAL_RESULTS.md && git commit -m 'chore(evals): record run'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Shelby evals on LangSmith")
    parser.add_argument("--repetitions", type=int, default=1, help="repeat each example N times (variance)")
    parser.add_argument("--no-save", action="store_true", help="don't write results to evals_results/ + docs/")
    args = parser.parse_args()
    outcome = run(repetitions=args.repetitions, save=not args.no_save)
    sys.exit(0 if outcome["passed"] else 1)
