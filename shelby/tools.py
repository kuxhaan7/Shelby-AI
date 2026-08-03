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
    {
        "name": "connect_mcp",
        "description": (
            "Connect a new external service to yourself by its MCP server URL — the same "
            "way a user adds a connector in Claude. Once connected, that server's tools become "
            "available to you on the next turn. Use this whenever the user gives you an MCP "
            "link (e.g. 'connect https://mcp.notion.com/mcp') or asks to hook up a service. "
            "The connection persists across restarts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short identifier for the service, e.g. 'notion', 'linear', 'apollo'."},
                "url": {"type": "string", "description": "The remote MCP endpoint URL (must start with http:// or https://)."},
                "token": {"type": "string", "description": "Optional bearer/access token for authenticated servers. Omit for no-auth servers."},
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list restricting which of the server's tools to expose. Omit to allow all.",
                },
            },
            "required": ["name", "url"],
        },
    },
    {
        "name": "list_mcp",
        "description": "List the external MCP services currently connected to Shelby (names, URLs, whether authenticated). Tokens are never shown.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "disconnect_mcp",
        "description": "Disconnect a previously connected MCP service by name (only servers added at runtime, not ones set via the environment).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the connected service to remove."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_webhook",
        "description": (
            "Register an incoming webhook that lets an external service trigger a saved skill "
            "by sending an HTTP POST. Use this when the user wants Shelby to react automatically "
            "to something outside the conversation — a new file landing somewhere, a GitHub push, "
            "a cron host, a form submission. The skill must already exist; use learn_skill first "
            "if it doesn't. The webhook's JSON body is passed to the skill as its keyword "
            "arguments. Returns the trigger path and a secret — show the secret to the user once "
            "and tell them it will not be shown again; they must send it back as the "
            "X-Shelby-Secret header (or a ?secret= query param) when triggering the webhook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short identifier for the webhook, e.g. 'new-dataset'."},
                "skill_name": {"type": "string", "description": "Name of an existing skill to run when this webhook fires."},
            },
            "required": ["name", "skill_name"],
        },
    },
    {
        "name": "list_webhooks",
        "description": "List all registered incoming webhooks and which skill each one triggers.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_webhook",
        "description": "Remove a registered webhook by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the webhook to remove."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "check_schema_drift",
        "description": (
            "Check a real CSV file's column structure against a remembered baseline, and "
            "report what changed: columns added, columns removed, or a column's inferred "
            "type flipping (e.g. numeric to text). The first time you check a given name, "
            "there's no baseline yet — it saves the current schema as the baseline and "
            "reports no drift. Every later check compares against it. Use this on a "
            "recurring export (especially one bound to a webhook) so a broken upstream "
            "change gets flagged before anyone notices bad data, instead of after."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the real CSV file to check."},
                "name": {"type": "string", "description": "Identifier for this recurring dataset, e.g. 'weekly-customers'. Defaults to the filename if omitted."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_quality_graph",
        "description": (
            "Run the LangGraph data-quality pipeline on a real CSV. Unlike the plain "
            "inspect/fix/evaluate skills, this one is a state machine that reacts to its own "
            "result: it repairs conservatively first, and if the resulting score is only high "
            "because missing values were imputed (invented), it escalates, quarantines those "
            "unrecoverable rows to a separate file for human review, and re-scores on the "
            "genuinely recoverable data. Use this when the user wants the rigorous version, or "
            "asks whether a quality score can be trusted. Returns the score history per pass "
            "including how much of each pass was imputed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the real CSV file to process."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_schema_baselines",
        "description": "List every dataset name Shelby is tracking for schema drift, and how many columns each baseline has.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "reset_schema_baseline",
        "description": "Reset a schema baseline to match a file's current structure — use this after a schema change was expected and approved, so it stops being reported as drift.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the real CSV file whose current schema becomes the new baseline."},
                "name": {"type": "string", "description": "Identifier for the dataset. Defaults to the filename if omitted."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_files",
        "description": "Find all supported files (PDF, DOCX, TXT, CSV, JSON, Markdown) in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to search. Defaults to current directory.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Search subdirectories recursively. Defaults to true.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of files to return. Defaults to 100.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "Read and extract text from a file (PDF, DOCX, TXT, CSV, JSON, Markdown).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "ingest_files",
        "description": "Ingest all files from a directory into the knowledge base. Automatically chunks and stores documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to ingest from. Defaults to current directory.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Search subdirectories recursively. Defaults to true.",
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum number of files to ingest. Defaults to 100.",
                },
            },
            "required": [],
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


def connect_mcp(args: dict) -> str:
    from .mcp import add_server
    result = add_server(
        name=args.get("name", ""),
        url=args.get("url", ""),
        token=args.get("token"),
        allowed_tools=args.get("allowed_tools"),
    )
    if not result.get("ok"):
        return f"Could not connect: {result.get('error')}"
    return (
        f"✅ Connected '{result['name']}'. Its tools are now available to me — "
        f"I'll use them from my next reply. (This connection persists across restarts.)"
    )


def list_mcp(_: dict) -> str:
    from .mcp import list_servers
    servers = list_servers()
    if not servers:
        return "No external MCP services are connected yet. Give me an MCP URL to connect one."
    lines = []
    for s in servers:
        auth = "authenticated" if s["authenticated"] else "no-auth"
        scope = f", tools: {', '.join(s['allowed_tools'])}" if s.get("allowed_tools") else ""
        lines.append(f"• {s['name']} — {s['url']} ({auth}, {s['source']}{scope})")
    return "\n".join(lines)


def disconnect_mcp(args: dict) -> str:
    from .mcp import remove_server
    result = remove_server(args.get("name", ""))
    if not result.get("ok"):
        return result.get("error", "Could not disconnect.")
    return f"Disconnected '{result['name']}'. Its tools are no longer available."


def create_webhook(args: dict, skill_registry=None) -> str:
    from .webhooks import registry as webhook_registry
    skill_name = args.get("skill_name", "")
    result = webhook_registry.create(args.get("name", ""), skill_name, skill_registry=skill_registry)
    if not result.get("ok"):
        return f"Could not create webhook: {result.get('error')}"
    return (
        f"Webhook '{result['name']}' created, wired to skill '{skill_name}'. "
        f"Trigger it with POST /webhooks/{result['name']} on the deployed URL, "
        f"header 'X-Shelby-Secret: {result['secret']}'. "
        f"Save this secret now — it will not be shown again."
    )


def list_webhooks(_: dict) -> str:
    from .webhooks import registry as webhook_registry
    hooks = webhook_registry.list_webhooks()
    if not hooks:
        return "No webhooks registered yet. Use create_webhook to add one."
    return "\n".join(f"• {h['name']} -> runs skill '{h['skill']}'" for h in hooks)


def delete_webhook(args: dict) -> str:
    from .webhooks import registry as webhook_registry
    result = webhook_registry.remove(args.get("name", ""))
    if not result.get("ok"):
        return result.get("error", "Could not delete webhook.")
    return f"Webhook '{result['name']}' removed."


def check_schema_drift(args: dict) -> str:
    from .dataquality import drift
    path = (args.get("path") or "").strip()
    if not path:
        return "No path provided."
    p = Path(path)
    if not p.exists() or not p.is_file():
        return f"No file found at {path}."
    try:
        result = drift.check(args.get("name", ""), p)
    except Exception as exc:
        return f"Could not check schema drift: {exc}"

    if result["first_run"]:
        return (
            f"No baseline existed for '{result['name']}' — saved the current schema as "
            f"the baseline ({len(result['columns'])} columns: {', '.join(result['columns'])}). "
            f"Future checks against this name will report drift against it."
        )
    if not result["drifted"]:
        return f"No schema drift for '{result['name']}' — {len(result['columns'])} columns, unchanged."

    lines = [f"Schema drift detected for '{result['name']}':"]
    if result["added"]:
        lines.append(f"  added columns: {', '.join(result['added'])}")
    if result["removed"]:
        lines.append(f"  removed columns: {', '.join(result['removed'])}")
    for tc in result["type_changed"]:
        lines.append(f"  '{tc['column']}' type changed: {tc['was']} -> {tc['now']}")
    return "\n".join(lines)


def find_files(args: dict) -> str:
    """Find all supported files in a directory."""
    from .file_ingestion import find_files as _find_files

    directory = args.get("directory", ".")
    recursive = args.get("recursive", True)
    max_results = args.get("max_results", 100)

    files = _find_files(directory, recursive=recursive)[:max_results]
    if not files:
        return f"No supported files found in {directory}"

    return "\n".join(f"- {f}" for f in files)


def read_file(args: dict) -> str:
    """Read and extract text from a file."""
    from .file_ingestion import read_file as _read_file

    path = args.get("path", "")
    if not path:
        return "Error: path is required"

    content, success = _read_file(path)
    if not success:
        return f"Error: Could not read file {path}"

    # Truncate very long content
    if len(content) > 8000:
        return content[:8000] + f"\n\n... (truncated, total length: {len(content)} chars)"

    return content


def ingest_files(args: dict, rag_store=None) -> str:
    """Ingest all files from a directory into the knowledge base."""
    from .file_ingestion import ingest_files as _ingest_files

    if rag_store is None:
        return "Error: RAG store not available"

    directory = args.get("directory", ".")
    recursive = args.get("recursive", True)
    max_files = args.get("max_files", 100)

    results = _ingest_files(rag_store, directory, recursive=recursive, max_files=max_files)

    msg = f"Ingestion complete: {results['ingested']} files ingested, {results['failed']} failed"
    if results["documents"]:
        msg += f", {len(results['documents'])} documents added to knowledge base"

    if results["errors"]:
        msg += "\n\nErrors:\n" + "\n".join(results["errors"][:5])

    return msg


def run_quality_graph(args: dict, outbox=None) -> str:
    from .dataquality import graph as dq_graph
    path = (args.get("path") or "").strip()
    if not path:
        return "No path provided."
    p = Path(path)
    if not p.exists() or not p.is_file():
        return f"No file found at {path}."
    try:
        r = dq_graph.run(p)
    except Exception as exc:
        return f"Quality graph failed: {exc}"

    lines = [
        f"LangGraph quality pipeline on {p.name}",
        f"  rows: {r['rows']}, columns: {len(r['columns'] or [])}",
        f"  score: {r['before']['overall']} -> {r['after']['overall']} (target {r['target_score']})",
        f"  passes run: {r['attempts']}",
    ]
    for h in r["history"]:
        lines.append(
            f"    pass {h['attempt']} ({h['strategy']}): score {h['score']}, "
            f"{h['imputed_share']*100:.1f}% of cells imputed"
        )
    if r["escalated"]:
        lines.append(
            f"  escalated: the first score was inflated by imputation, so "
            f"{r['quarantined']} unrecoverable row(s) were quarantined and the data re-scored."
        )
        if r["quarantine_file"]:
            lines.append(f"  quarantined rows written to: {r['quarantine_file']}")
            if outbox is not None:
                outbox.append(str(Path(r["quarantine_file"]).resolve()))
    else:
        lines.append("  no escalation needed: the score was earned, not imputed.")
    if r["changelog"]:
        lines.append("  changelog:")
        lines.extend(f"    {c}" for c in r["changelog"])
    return "\n".join(lines)


def list_schema_baselines(_: dict) -> str:
    from .dataquality import drift
    baselines = drift.list_baselines()
    if not baselines:
        return "No schema baselines saved yet. Run check_schema_drift on a file to create one."
    return "\n".join(f"• {b['name']} — {len(b['columns'])} columns" for b in baselines)


def reset_schema_baseline(args: dict) -> str:
    from .dataquality import drift
    path = (args.get("path") or "").strip()
    if not path:
        return "No path provided."
    p = Path(path)
    if not p.exists() or not p.is_file():
        return f"No file found at {path}."
    result = drift.update_baseline(args.get("name", ""), p)
    return f"Baseline for '{result['name']}' reset to the current schema ({len(result['columns'])} columns)."


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
        case "connect_mcp":
            return connect_mcp(tool_input)
        case "list_mcp":
            return list_mcp(tool_input)
        case "disconnect_mcp":
            return disconnect_mcp(tool_input)
        case "create_webhook":
            return create_webhook(tool_input, skill_registry)
        case "list_webhooks":
            return list_webhooks(tool_input)
        case "delete_webhook":
            return delete_webhook(tool_input)
        case "run_quality_graph":
            return run_quality_graph(tool_input, outbox)
        case "check_schema_drift":
            return check_schema_drift(tool_input)
        case "list_schema_baselines":
            return list_schema_baselines(tool_input)
        case "reset_schema_baseline":
            return reset_schema_baseline(tool_input)
        case "find_files":
            return find_files(tool_input)
        case "read_file":
            return read_file(tool_input)
        case "ingest_files":
            return ingest_files(tool_input, rag_store)
        case _:
            return f"Unknown tool: {tool_name}"
