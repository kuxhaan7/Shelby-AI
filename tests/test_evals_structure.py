"""Structural tests for the eval module — no API key needed."""

from shelby.evals.dataset import EVAL_DATASET
from shelby.evals.evaluators import (
    _FAITHFULNESS_PROMPT,
    _RELEVANCE_PROMPT,
    _CONCISENESS_PROMPT,
    evaluate_faithfulness,
    evaluate_answer_relevance,
)


def test_dataset_not_empty():
    assert len(EVAL_DATASET) >= 4


def test_dataset_fields():
    for sample in EVAL_DATASET:
        assert "question" in sample
        assert "context" in sample
        assert "ground_truth" in sample
        assert sample["question"].strip()
        assert sample["context"].strip()


def test_prompts_have_required_variables():
    faith_vars = _FAITHFULNESS_PROMPT.input_variables
    assert "context" in faith_vars
    assert "prediction" in faith_vars

    rel_vars = _RELEVANCE_PROMPT.input_variables
    assert "question" in rel_vars
    assert "prediction" in rel_vars

    con_vars = _CONCISENESS_PROMPT.input_variables
    assert "question" in con_vars
    assert "prediction" in con_vars


def test_evaluator_functions_are_callable():
    assert callable(evaluate_faithfulness)
    assert callable(evaluate_answer_relevance)
