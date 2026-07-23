"""Remote MCP connector.

Lets Shelby use *hosted* MCP servers (Gmail, Apollo, Google Calendar, Notion,
Linear, … any service exposing a remote MCP endpoint). Claude connects to these
servers itself, server-side, via Anthropic's MCP connector — so there is no
subprocess to manage and no OAuth dance running inside our container. We just
tell the Messages API which servers to attach and hand over a bearer token.

Everything is declared through environment variables so no URLs or tokens are
ever committed to the repo. Two ways to declare servers:

1. `SHELBY_MCP_SERVERS` — a JSON array, for full control:

     [
       {"name": "apollo",
        "url": "https://mcp.apollo.io/mcp",
        "token_env": "APOLLO_MCP_TOKEN",
        "allowed_tools": ["apollo_contacts_search", "apollo_people_match"]},
       {"name": "gmail",
        "url": "https://mcp.example.com/gmail/sse",
        "token_env": "GMAIL_MCP_TOKEN"}
     ]

   Per entry:
     - name (required)  — short identifier Shelby shows the user.
     - url  (required)  — the remote MCP endpoint (SSE or streamable HTTP).
     - token_env        — name of ANOTHER env var holding the bearer token
                          (preferred — keeps the secret out of this JSON).
     - authorization_token — the literal token (works, but prefer token_env).
     - allowed_tools    — optional list restricting which of the server's tools
                          are exposed (omit to allow all).

2. Convenience single-server vars — no JSON needed:

     SHELBY_MCP_<NAME>_URL      (required to register the server)
     SHELBY_MCP_<NAME>_TOKEN    (optional bearer token)

   e.g. SHELBY_MCP_APOLLO_URL + SHELBY_MCP_APOLLO_TOKEN registers "apollo".

3. At runtime, by URL — exactly like Claude's "add a connector" flow. Shelby's
   `connect_mcp` tool (or the /mcp API) registers a server on the fly:

     connect_mcp(name="notion", url="https://mcp.notion.com/mcp", token="…")

   Runtime servers are persisted to a JSON registry under SHELBY_DATA_DIR (the
   volume), so they survive redeploys. They can be listed and removed too.

If nothing is configured, the connector is simply inactive and Shelby behaves
exactly as before.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Anthropic beta flag required to attach remote MCP servers to a Messages call.
MCP_BETA = "mcp-client-2025-04-04"

_JSON_ENV = "SHELBY_MCP_SERVERS"
_PREFIX = "SHELBY_MCP_"
_SUFFIX_URL = "_URL"
_SUFFIX_TOKEN = "_TOKEN"


def _resolve_token(entry: dict) -> str | None:
    """Return the bearer token for a server entry, or None."""
    token_env = entry.get("token_env")
    if token_env:
        tok = os.getenv(token_env)
        if not tok:
            log.warning(
                "MCP server %r references token_env=%r but that env var is unset.",
                entry.get("name"), token_env,
            )
        return tok or None
    tok = entry.get("authorization_token")
    return tok or None


def _from_json_env() -> list[dict]:
    """Parse the SHELBY_MCP_SERVERS JSON array into normalised entries."""
    raw = os.getenv(_JSON_ENV, "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("%s is not valid JSON (%s) — ignoring it.", _JSON_ENV, exc)
        return []
    if not isinstance(data, list):
        log.error("%s must be a JSON array of server objects — ignoring it.", _JSON_ENV)
        return []

    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name, url = item.get("name"), item.get("url")
        if not name or not url:
            log.warning("Skipping MCP server with missing name/url: %r", item)
            continue
        out.append({
            "name": str(name),
            "url": str(url),
            "token": _resolve_token(item),
            "allowed_tools": item.get("allowed_tools"),
        })
    return out


def _from_convenience_env() -> list[dict]:
    """Parse SHELBY_MCP_<NAME>_URL / _TOKEN convenience variables."""
    out: list[dict] = []
    for key, url in os.environ.items():
        if not (key.startswith(_PREFIX) and key.endswith(_SUFFIX_URL)):
            continue
        if key == _JSON_ENV:  # not a <NAME>_URL var
            continue
        middle = key[len(_PREFIX):-len(_SUFFIX_URL)]
        if not middle:
            continue
        name = middle.lower()
        token = os.getenv(f"{_PREFIX}{middle}{_SUFFIX_TOKEN}")
        out.append({
            "name": name,
            "url": url.strip(),
            "token": (token or "").strip() or None,
            "allowed_tools": None,
        })
    return out


# ── Runtime registry (servers added by URL during a conversation) ────────────

def _load_runtime() -> list[dict]:
    """Load MCP servers registered at runtime from the persistent JSON file."""
    from ..paths import mcp_file
    path = mcp_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Could not read MCP registry %s (%s) — ignoring it.", path, exc)
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if isinstance(item, dict) and item.get("name") and item.get("url"):
            out.append({
                "name": str(item["name"]),
                "url": str(item["url"]),
                "token": item.get("token") or None,
                "allowed_tools": item.get("allowed_tools"),
            })
    return out


def _save_runtime(entries: list[dict]) -> None:
    from ..paths import mcp_file
    path = mcp_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2))


def _norm_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip().lower())


def add_server(
    name: str, url: str, token: str | None = None,
    allowed_tools: list[str] | None = None,
) -> dict:
    """Register (or update) an MCP server by URL at runtime. Persisted to disk.

    Returns {"ok": True, "name": ...} or {"ok": False, "error": ...}.
    """
    name = _norm_name(name or "")
    url = (url or "").strip()
    if not name:
        return {"ok": False, "error": "A server name is required."}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"ok": False, "error": "URL must be a full http(s):// MCP endpoint."}
    if name in {e["name"] for e in _env_entries()}:
        return {"ok": False, "error": f"'{name}' is defined in the environment and can't be overridden at runtime."}

    entries = _load_runtime()
    entries = [e for e in entries if e["name"] != name]  # upsert
    entries.append({
        "name": name, "url": url,
        "token": (token or "").strip() or None,
        "allowed_tools": allowed_tools or None,
    })
    _save_runtime(entries)
    return {"ok": True, "name": name}


def remove_server(name: str) -> dict:
    """Remove a runtime-registered MCP server by name."""
    name = _norm_name(name or "")
    entries = _load_runtime()
    kept = [e for e in entries if e["name"] != name]
    if len(kept) == len(entries):
        if name in {e["name"] for e in _env_entries()}:
            return {"ok": False, "error": f"'{name}' is set via the environment — remove it there, not at runtime."}
        return {"ok": False, "error": f"No runtime server named '{name}'."}
    _save_runtime(kept)
    return {"ok": True, "name": name}


def _env_entries() -> list[dict]:
    """Servers declared through environment variables (JSON + convenience)."""
    entries = _from_json_env()
    seen = {e["name"] for e in entries}
    for e in _from_convenience_env():
        if e["name"] not in seen:
            entries.append(e)
            seen.add(e["name"])
    return entries


def _entries() -> list[dict]:
    """All MCP servers: environment-defined first, then runtime-registered.

    De-duplicated by name; environment definitions take precedence.
    """
    entries = _env_entries()
    seen = {e["name"] for e in entries}
    for e in _load_runtime():
        if e["name"] not in seen:
            entries.append(e)
            seen.add(e["name"])
    return entries


def mcp_servers() -> list[dict]:
    """Build the `mcp_servers` payload for the Anthropic Messages API.

    Returns an empty list when nothing is configured (connector inactive).
    """
    payload: list[dict] = []
    for e in _entries():
        server: dict = {"type": "url", "url": e["url"], "name": e["name"]}
        if e.get("token"):
            server["authorization_token"] = e["token"]
        allowed = e.get("allowed_tools")
        if allowed:
            server["tool_configuration"] = {"enabled": True, "allowed_tools": allowed}
        payload.append(server)
    return payload


def list_servers() -> list[dict]:
    """Structured view of every connected server (tokens redacted)."""
    env_names = {e["name"] for e in _env_entries()}
    out = []
    for e in _entries():
        out.append({
            "name": e["name"],
            "url": e["url"],
            "authenticated": bool(e.get("token")),
            "allowed_tools": e.get("allowed_tools"),
            "source": "environment" if e["name"] in env_names else "runtime",
        })
    return out


def describe_servers() -> str:
    """Human-readable summary of connected MCP servers (for the system prompt)."""
    entries = _entries()
    if not entries:
        return ""
    lines = []
    for e in entries:
        auth = "authenticated" if e.get("token") else "no-auth"
        scope = (
            f", tools: {', '.join(e['allowed_tools'])}"
            if e.get("allowed_tools") else ""
        )
        lines.append(f"- {e['name']} ({auth}){scope}")
    return "\n".join(lines)
