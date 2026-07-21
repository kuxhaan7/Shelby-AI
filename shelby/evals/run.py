"""Run the Shelby eval suite and print a results table."""

from __future__ import annotations

import os
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

from ..agent import ShelbyAgent
from ..rag.store import RagStore
from .dataset import EVAL_DATASET
from .evaluators import evaluate_answer_relevance, evaluate_faithfulness

console = Console()


def _agent() -> ShelbyAgent:
    store = RagStore()
    return ShelbyAgent(rag_store=store)


def run_evals(verbose: bool = False) -> list[dict[str, Any]]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY not set — cannot run evals.[/red]")
        sys.exit(1)

    ag = _agent()
    results = []

    for sample in EVAL_DATASET:
        q = sample["question"]
        ctx = sample["context"]
        console.print(f"\n[bold cyan]Q:[/bold cyan] {q}")

        messages = [
            {
                "role": "user",
                "content": f"Context:\n{ctx}\n\nQuestion: {q}",
            }
        ]
        prediction = ag.chat(messages)
        console.print(f"[green]A:[/green] {prediction}")

        faith = evaluate_faithfulness(prediction, ctx)
        rel = evaluate_answer_relevance(q, prediction)

        row = {
            "question": q,
            "prediction": prediction,
            "faithfulness": faith["score"],
            "relevance": rel["score"],
        }
        if verbose:
            row["faith_reason"] = faith["reasoning"]
            row["rel_reason"] = rel["reasoning"]
        results.append(row)

    _print_table(results)
    return results


def _print_table(results: list[dict[str, Any]]) -> None:
    table = Table(title="Shelby Eval Results", show_lines=True)
    table.add_column("Question", style="cyan", max_width=40)
    table.add_column("Faithfulness", justify="center")
    table.add_column("Relevance", justify="center")

    for r in results:
        def _fmt(score):
            if score is None:
                return "N/A"
            pct = int(score * 100) if score <= 1 else int(score)
            color = "green" if pct >= 70 else "yellow" if pct >= 40 else "red"
            return f"[{color}]{pct}%[/{color}]"

        table.add_row(r["question"][:60], _fmt(r["faithfulness"]), _fmt(r["relevance"]))

    console.print(table)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run Shelby evals")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    run_evals(verbose=args.verbose)
