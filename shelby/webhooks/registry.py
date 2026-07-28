"""Persistent registry of incoming webhooks.

A webhook is a name bound to a saved skill and a shared secret. An external
service (GitHub, a cron host, a form backend, anything that can send an HTTP
POST) hits `POST /webhooks/<name>` with that secret, and Shelby runs the
bound skill with the request body as its keyword arguments.

Registered entirely at runtime, through the create_webhook tool or the
`/webhooks` REST endpoint — nothing is declared through environment
variables, since each webhook is user data, not deployment config. Entries
persist to a JSON file under SHELBY_DATA_DIR so they survive restarts when a
volume is mounted.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from typing import Any

log = logging.getLogger(__name__)


def _norm_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "").strip().lower())


def _load() -> dict[str, dict[str, Any]]:
    from ..paths import webhooks_file
    path = webhooks_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Could not read webhook registry %s (%s) — ignoring it.", path, exc)
        return {}


def _save(hooks: dict[str, dict[str, Any]]) -> None:
    from ..paths import webhooks_file
    path = webhooks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hooks, indent=2))


def create(
    name: str, skill_name: str, secret: str | None = None, skill_registry=None
) -> dict[str, Any]:
    """Register (or replace) a webhook bound to *skill_name*.

    Returns {"ok": True, "name", "secret"} on success, {"ok": False, "error"}
    otherwise. Generates a random secret when one isn't supplied.
    """
    name = _norm_name(name)
    skill_name = (skill_name or "").strip()
    if not name:
        return {"ok": False, "error": "A webhook name is required."}
    if not skill_name:
        return {"ok": False, "error": "A skill_name is required — the skill to run when this webhook fires."}
    if skill_registry is not None and not skill_registry.exists(skill_name):
        return {"ok": False, "error": f"No skill named '{skill_name}'. Save it first with learn_skill."}

    hooks = _load()
    secret = (secret or "").strip() or secrets.token_urlsafe(24)
    hooks[name] = {"skill": skill_name, "secret": secret}
    _save(hooks)
    return {"ok": True, "name": name, "secret": secret}


def remove(name: str) -> dict[str, Any]:
    """Delete a webhook by name."""
    name = _norm_name(name)
    hooks = _load()
    if name not in hooks:
        return {"ok": False, "error": f"No webhook named '{name}'."}
    del hooks[name]
    _save(hooks)
    return {"ok": True, "name": name}


def list_webhooks() -> list[dict[str, Any]]:
    """Structured view of every registered webhook (secrets never included)."""
    hooks = _load()
    return [{"name": name, "skill": h.get("skill", "")} for name, h in sorted(hooks.items())]


def get(name: str) -> dict[str, Any] | None:
    """Raw entry for a webhook (includes the secret) — internal use only."""
    return _load().get(_norm_name(name))


def verify(name: str, provided_secret: str) -> bool:
    """Constant-time check that *provided_secret* matches the stored one."""
    hook = get(name)
    if not hook:
        return False
    return hmac.compare_digest(hook.get("secret", ""), provided_secret or "")
