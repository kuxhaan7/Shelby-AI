"""LangChain-based evaluators for Shelby response quality."""

from __future__ import annotations

import os
from typing import Any

from langchain.evaluation import load_evaluator
from langchain_anthropic import ChatAnthropic


def _llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=os.getenv("SHELBY_MODEL", "claude-sonnet-5"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
    )


def evaluate_faithfulness(prediction: str, context: str) -> dict[str, Any]:
    """Score whether the prediction is faithful to the provided context."""
    evaluator = load_evaluator(
        "labeled_criteria",
        llm=_llm(),
        criteria={
            "faithfulness": (
                "Does the answer accurately reflect only what is stated in the reference context, "
                "without hallucinating facts not present there?"
            )
        },
    )
    result = evaluator.evaluate_strings(
        prediction=prediction,
        reference=context,
        input="",
    )
    return {"score": result.get("score"), "reasoning": result.get("reasoning")}


def evaluate_answer_relevance(question: str, prediction: str) -> dict[str, Any]:
    """Score whether the answer actually addresses the question."""
    evaluator = load_evaluator(
        "criteria",
        llm=_llm(),
        criteria={
            "relevance": "Does the answer directly and completely address the question?"
        },
    )
    result = evaluator.evaluate_strings(prediction=prediction, input=question)
    return {"score": result.get("score"), "reasoning": result.get("reasoning")}


def evaluate_conciseness(question: str, prediction: str) -> dict[str, Any]:
    """Score whether the answer is appropriately concise."""
    evaluator = load_evaluator(
        "criteria",
        llm=_llm(),
        criteria={
            "conciseness": "Is the answer concise and free of unnecessary filler words?"
        },
    )
    result = evaluator.evaluate_strings(prediction=prediction, input=question)
    return {"score": result.get("score"), "reasoning": result.get("reasoning")}
