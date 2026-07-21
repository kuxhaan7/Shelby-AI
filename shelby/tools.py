"""Tool definitions for Claude function calling."""

import json
import math
from datetime import datetime, timezone
from typing import Any

# ── Tool schemas (passed to Anthropic API) ──────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_current_time",
        "description": "Return the current UTC date and time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "calculate",
        "description": "Evaluate a safe mathematical expression and return the result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A Python-evaluable math expression, e.g. '2 ** 10' or 'math.sqrt(144)'.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Search Shelby's persistent knowledge base (RAG store) for context "
            "relevant to the query. Returns the top matching passages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."},
                "n_results": {"type": "integer", "description": "Number of results (default 3).", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": "Store a key fact or note into Shelby's knowledge base for future retrieval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The fact or note to remember."},
                "source": {"type": "string", "description": "Optional label for where this came from."},
            },
            "required": ["text"],
        },
    },
]


# ── Tool implementations ─────────────────────────────────────────────────────

def get_current_time(_: dict) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def calculate(args: dict) -> str:
    expr = args["expression"]
    # Restrict to a safe subset — no builtins except math
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    allowed["abs"] = abs
    try:
        result = eval(expr, {"__builtins__": {}}, allowed)  # noqa: S307
        return str(result)
    except Exception as exc:
        return f"Error: {exc}"


def search_knowledge_base(args: dict, rag_store=None) -> str:
    if rag_store is None:
        return "Knowledge base not initialised."
    results = rag_store.query(args["query"], n_results=args.get("n_results", 3))
    if not results:
        return "No relevant passages found."
    return "\n\n---\n\n".join(
        f"[{r['source']}]\n{r['text']}" for r in results
    )


def remember(args: dict, rag_store=None) -> str:
    if rag_store is None:
        return "Knowledge base not initialised."
    rag_store.add(args["text"], source=args.get("source", "user"))
    return "Remembered."


# ── Dispatcher ───────────────────────────────────────────────────────────────

def dispatch(tool_name: str, tool_input: dict, rag_store=None) -> Any:
    if tool_name == "get_current_time":
        return get_current_time(tool_input)
    if tool_name == "calculate":
        return calculate(tool_input)
    if tool_name == "search_knowledge_base":
        return search_knowledge_base(tool_input, rag_store)
    if tool_name == "remember":
        return remember(tool_input, rag_store)
    return f"Unknown tool: {tool_name}"
