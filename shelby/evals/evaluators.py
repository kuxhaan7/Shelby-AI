"""LangChain 1.x evaluators for Shelby response quality (LCEL / LLM-as-judge)."""

from __future__ import annotations

import os
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

_SCHEMA = '{"score": <0.0-1.0>, "reasoning": "<one sentence>"}'

_FAITHFULNESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are an impartial judge. Respond ONLY with valid JSON: " + _SCHEMA),
    ("human", (
        "Context:\n{context}\n\n"
        "Answer:\n{prediction}\n\n"
        "Score 1.0 if the answer contains only facts from the context (no hallucinations), "
        "0.0 if it invents facts not present there."
    )),
])

_RELEVANCE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are an impartial judge. Respond ONLY with valid JSON: " + _SCHEMA),
    ("human", (
        "Question:\n{question}\n\n"
        "Answer:\n{prediction}\n\n"
        "Score 1.0 if the answer directly and completely addresses the question, "
        "0.0 if it is off-topic or incomplete."
    )),
])

_CONCISENESS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are an impartial judge. Respond ONLY with valid JSON: " + _SCHEMA),
    ("human", (
        "Question:\n{question}\n\n"
        "Answer:\n{prediction}\n\n"
        "Score 1.0 if the answer is concise and free of filler, "
        "0.0 if it is verbose or padded."
    )),
])


def _llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=os.getenv("SHELBY_EVAL_MODEL", "claude-haiku-4-5-20251001"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0,
        max_tokens=256,
    )


def _run_judge(prompt: ChatPromptTemplate, variables: dict) -> dict[str, Any]:
    chain = prompt | _llm() | JsonOutputParser()
    try:
        result = chain.invoke(variables)
        return {"score": float(result.get("score", 0)), "reasoning": result.get("reasoning", "")}
    except Exception as exc:
        return {"score": None, "reasoning": f"Evaluator error: {exc}"}


def evaluate_faithfulness(prediction: str, context: str) -> dict[str, Any]:
    """Score whether the prediction is faithful to the provided context (0–1)."""
    return _run_judge(_FAITHFULNESS_PROMPT, {"context": context, "prediction": prediction})


def evaluate_answer_relevance(question: str, prediction: str) -> dict[str, Any]:
    """Score whether the answer directly addresses the question (0–1)."""
    return _run_judge(_RELEVANCE_PROMPT, {"question": question, "prediction": prediction})


def evaluate_conciseness(question: str, prediction: str) -> dict[str, Any]:
    """Score whether the answer is appropriately concise (0–1)."""
    return _run_judge(_CONCISENESS_PROMPT, {"question": question, "prediction": prediction})
