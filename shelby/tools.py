"""Tool definitions and implementations for Shelby's Claude function-calling loop."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Tool schemas ─────────────────────────────────────────────────────────────

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
                    "description": "A Python-evaluable math expression, e.g. '2**10' or 'math.sqrt(144)'.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the live web using Tavily and return relevant results. "
            "Use for current events, facts you don't know, or any real-time data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Search Shelby's persistent knowledge base (RAG) for previously stored context. "
            "Always try this before web_search for topics you may have seen before."
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
        "description": "Store a passage or note into Shelby's semantic knowledge base for future retrieval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to remember."},
                "source": {"type": "string", "description": "Optional label for provenance."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "write_memory",
        "description": (
            "Write a structured key-value fact into Shelby's persistent memory. "
            "Use for user preferences, important facts, or anything Shelby should always know. "
            "Example: key='user_name', value='Kaushik'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short identifier for the memory, e.g. 'user_timezone'."},
                "value": {"type": "string", "description": "The value to store."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "read_memory",
        "description": "Read a specific key from Shelby's structured memory, or list all keys if no key given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The memory key to retrieve. Omit to list all keys."},
            },
            "required": [],
        },
    },
    {
        "name": "learn_skill",
        "description": (
            "Write and save a new Python skill that Shelby can run later. "
            "The code MUST define a `run(**kwargs) -> str` function. "
            "Skills persist across sessions. Use this to teach Shelby reusable capabilities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short snake_case name for the skill, e.g. 'get_weather'."},
                "description": {"type": "string", "description": "One sentence describing what the skill does."},
                "code": {
                    "type": "string",
                    "description": (
                        "Complete Python code defining `run(**kwargs) -> str`. "
                        "May import stdlib or requests. Must return a string."
                    ),
                },
            },
            "required": ["name", "description", "code"],
        },
    },
    {
        "name": "run_skill",
        "description": "Execute a previously learned skill by name and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name (as saved with learn_skill)."},
                "args": {
                    "type": "object",
                    "description": "Arguments to pass to the skill's run() function.",
                    "default": {},
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_skills",
        "description": "List all skills Shelby has learned, with their names and descriptions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a learned skill to run automatically on a cron schedule. "
            "The skill must already exist (use learn_skill first). "
            "Example: run 'fetch_headlines' every morning at 8am UTC → cron='0 8 * * *'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name for this scheduled task, e.g. 'morning_headlines'.",
                },
                "cron": {
                    "type": "string",
                    "description": "Standard 5-field cron expression, e.g. '0 9 * * 1-5' for weekdays at 9am UTC.",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to run (must exist in the skill registry).",
                },
                "kwargs": {
                    "type": "object",
                    "description": "Optional keyword arguments to pass to the skill's run() function.",
                    "default": {},
                },
            },
            "required": ["name", "cron", "skill_name"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List all scheduled cron tasks, including their schedule, skill, and next run time.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_task",
        "description": "Cancel and remove a scheduled task by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the task to cancel."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "kaggle_search",
        "description": (
            "Search Kaggle for real public datasets by keyword. Use this when asked to find, "
            "test against, or benchmark on real-world data. Requires the KAGGLE_API_TOKEN "
            "environment variable to be configured — if it returns a setup error, tell the "
            "user how to get a token (kaggle.com/settings/api) and set it in the environment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords, e.g. 'airbnb new york' or 'hospital readmission'."},
                "max_results": {"type": "integer", "description": "Max results to return (default 8).", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kaggle_download",
        "description": (
            "Download a Kaggle dataset by its ref (owner/dataset-name, from kaggle_search "
            "results) and automatically profile every CSV file found for data-quality issues "
            "(nulls, duplicates, currency/percent stored as text). Requires KAGGLE_API_TOKEN."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_ref": {"type": "string", "description": "Dataset ref, e.g. 'dgomonov/new-york-city-airbnb-open-data'."},
                "file_name": {"type": "string", "description": "Optional: download only this one file instead of the whole dataset."},
            },
            "required": ["dataset_ref"],
        },
    },
    {
        "name": "send_file",
        "description": (
            "Deliver a file that exists on disk to the user (Telegram sends it as a document; "
            "the web UI shows a download button). Use this whenever the user asks for a file — "
            "e.g. a cleaned CSV you produced with fix_dataset, or a dataset you downloaded. "
            "Pass the exact path. Files must live under Shelby's data directory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to send, e.g. the '_clean.csv' returned by fix_dataset."},
            },
            "required": ["path"],
        },
    },
]


# ── Implementations ──────────────────────────────────────────────────────────

def get_current_time(_: dict) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def calculate(args: dict) -> str:
    expr = args["expression"]
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    allowed["abs"] = abs
    try:
        result = eval(expr, {"__builtins__": {}}, allowed)  # noqa: S307
        return str(result)
    except Exception as exc:
        return f"Error: {exc}"


def web_search(args: dict) -> str:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "TAVILY_API_KEY is not set — web search is unavailable."
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=args["query"],
            max_results=min(int(args.get("max_results", 5)), 10),
            include_answer=True,
        )
        parts = []
        if response.get("answer"):
            parts.append(f"Summary: {response['answer']}\n")
        for r in response.get("results", []):
            parts.append(f"[{r.get('title', 'untitled')}]\n{r.get('url', '')}\n{r.get('content', '')}")
        return "\n\n---\n\n".join(parts) if parts else "No results found."
    except Exception as exc:
        return f"Web search error: {exc}"


def search_knowledge_base(args: dict, rag_store=None) -> str:
    if rag_store is None:
        return "Knowledge base not initialised."
    results = rag_store.query(args["query"], n_results=args.get("n_results", 3))
    if not results:
        return "No relevant passages found."
    return "\n\n---\n\n".join(f"[{r['source']}]\n{r['text']}" for r in results)


def remember(args: dict, rag_store=None) -> str:
    if rag_store is None:
        return "Knowledge base not initialised."
    rag_store.add(args["text"], source=args.get("source", "shelby"))
    return "Stored in semantic memory."


def write_memory(args: dict, notes_store=None) -> str:
    if notes_store is None:
        return "Memory store not initialised."
    notes_store.write(args["key"], args["value"])
    return f"Memory written: {args['key']} = {args['value']}"


def read_memory(args: dict, notes_store=None) -> str:
    if notes_store is None:
        return "Memory store not initialised."
    key = args.get("key", "").strip()
    if not key:
        return notes_store.dump()
    result = notes_store.read(key)
    return result if result is not None else f"No memory found for key '{key}'."


def learn_skill(args: dict, skill_registry=None) -> str:
    if skill_registry is None:
        return "Skill registry not initialised."
    path = skill_registry.save(args["name"], args["description"], args["code"])
    return f"Skill '{args['name']}' saved to {path}. Run it with run_skill."


def run_skill(args: dict, skill_registry=None) -> str:
    if skill_registry is None:
        return "Skill registry not initialised."
    return skill_registry.run(args["name"], args.get("args") or {})


def list_skills(_: dict, skill_registry=None) -> str:
    if skill_registry is None:
        return "Skill registry not initialised."
    skills = skill_registry.list()
    if not skills:
        return "No skills learned yet. Use learn_skill to teach Shelby new capabilities."
    return "\n".join(f"• {s['name']}: {s['description']}" for s in skills)


def schedule_task(args: dict, task_scheduler=None) -> str:
    if task_scheduler is None:
        return "Task scheduler not initialised."
    return task_scheduler.schedule(
        name=args["name"],
        cron=args["cron"],
        skill_name=args["skill_name"],
        kwargs=args.get("kwargs") or {},
    )


def list_tasks(_: dict, task_scheduler=None) -> str:
    if task_scheduler is None:
        return "Task scheduler not initialised."
    tasks = task_scheduler.list_tasks()
    if not tasks:
        return "No scheduled tasks. Use schedule_task to create one."
    lines = []
    for t in tasks:
        lines.append(f"• {t['name']} | cron: {t['cron']} | skill: {t['skill']} | next: {t['next_run']}")
    return "\n".join(lines)


def cancel_task(args: dict, task_scheduler=None) -> str:
    if task_scheduler is None:
        return "Task scheduler not initialised."
    return task_scheduler.cancel(args["name"])


def kaggle_search(args: dict) -> str:
    from .integrations.kaggle_client import search
    result = search(args["query"], int(args.get("max_results", 8)))
    if "error" in result:
        return result["error"]
    if not result["results"]:
        return f"No Kaggle datasets found for '{args['query']}'."
    lines = []
    for d in result["results"]:
        ref = d.get("ref") or d.get("datasetSlugNullable") or d.get("id") or "?"
        title = d.get("title") or d.get("titleNullable") or ref
        size = d.get("size") or d.get("totalBytesNullable") or "?"
        lines.append(f"• {ref} — {title} ({size})")
    return "\n".join(lines)


_MAX_SEND_BYTES = 45 * 1024 * 1024  # Telegram bot sendDocument limit is 50MB


def send_file(args: dict, outbox=None) -> str:
    from .paths import DATA_DIR

    raw = (args.get("path") or "").strip()
    if not raw:
        return "No path provided."
    p = Path(raw)
    if not p.exists() or not p.is_file():
        return f"No file found at {raw}."
    # Security: only deliver files under Shelby's own data directory.
    try:
        p.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return f"Refusing to send {raw}: outside the data directory."
    size = p.stat().st_size
    if size > _MAX_SEND_BYTES:
        return f"'{p.name}' is {size // (1024*1024)}MB — too large to send (limit ~45MB)."
    if outbox is None:
        return f"'{p.name}' is ready at {raw}, but no delivery channel is available right now."
    outbox.append(str(p.resolve()))
    return f"✅ Queued '{p.name}' ({size:,} bytes) for delivery to the user."


def kaggle_download(args: dict) -> str:
    from .dataquality.quickprofile import quick_profile_bytes
    from .integrations.kaggle_client import download

    result = download(args["dataset_ref"], args.get("file_name"))
    if "error" in result:
        return result["error"]

    lines = [f"Downloaded '{result['dataset']}' → {result['path']}", "Files:"]
    for f in result["files"]:
        lines.append(f"  {f}")
        if f.lower().endswith((".csv", ".csv.gz")):
            try:
                raw = Path(f).read_bytes()
                profile = quick_profile_bytes(raw, Path(f).name)
                if "error" not in profile:
                    lines.append(
                        f"    -> {profile['rows']:,} rows, {len(profile['columns'])} cols, "
                        f"quality score {profile['scores']['overall']}/100"
                    )
                    for issue in profile["issues"][:3]:
                        lines.append(f"    x {issue}")
            except Exception:
                pass
    return "\n".join(lines)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def dispatch(
    tool_name: str,
    tool_input: dict,
    rag_store=None,
    notes_store=None,
    skill_registry=None,
    task_scheduler=None,
    outbox=None,
) -> Any:
    match tool_name:
        case "get_current_time":
            return get_current_time(tool_input)
        case "calculate":
            return calculate(tool_input)
        case "web_search":
            return web_search(tool_input)
        case "search_knowledge_base":
            return search_knowledge_base(tool_input, rag_store)
        case "remember":
            return remember(tool_input, rag_store)
        case "write_memory":
            return write_memory(tool_input, notes_store)
        case "read_memory":
            return read_memory(tool_input, notes_store)
        case "learn_skill":
            return learn_skill(tool_input, skill_registry)
        case "run_skill":
            return run_skill(tool_input, skill_registry)
        case "list_skills":
            return list_skills(tool_input, skill_registry)
        case "schedule_task":
            return schedule_task(tool_input, task_scheduler)
        case "list_tasks":
            return list_tasks(tool_input, task_scheduler)
        case "cancel_task":
            return cancel_task(tool_input, task_scheduler)
        case "kaggle_search":
            return kaggle_search(tool_input)
        case "kaggle_download":
            return kaggle_download(tool_input)
        case "send_file":
            return send_file(tool_input, outbox)
        case _:
            return f"Unknown tool: {tool_name}"
