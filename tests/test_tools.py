"""Unit tests for shelby.tools — no API key needed."""

import pytest
from shelby.tools import calculate, dispatch, get_current_time, remember, search_knowledge_base


def test_get_current_time():
    result = get_current_time({})
    assert "UTC" in result
    assert len(result) > 10


def test_calculate_basic():
    assert calculate({"expression": "2 + 2"}) == "4"
    assert calculate({"expression": "2 ** 10"}) == "1024"


def test_calculate_math_functions():
    import math
    result = calculate({"expression": "sqrt(144)"})
    assert result == "12.0"


def test_calculate_bad_expression():
    result = calculate({"expression": "import os"})
    assert "Error" in result


def test_calculate_division_by_zero():
    result = calculate({"expression": "1/0"})
    assert "Error" in result


def test_search_knowledge_base_no_store():
    result = search_knowledge_base({"query": "anything"}, rag_store=None)
    assert "not initialised" in result


def test_remember_no_store():
    result = remember({"text": "anything"}, rag_store=None)
    assert "not initialised" in result


def test_dispatch_unknown_tool():
    result = dispatch("nonexistent_tool", {})
    assert "Unknown tool" in result


def test_dispatch_get_current_time():
    result = dispatch("get_current_time", {})
    assert "UTC" in result


def test_dispatch_calculate():
    result = dispatch("calculate", {"expression": "3 * 7"})
    assert result == "21"
