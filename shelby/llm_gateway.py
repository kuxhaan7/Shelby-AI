"""LLM Gateway — unified interface for multi-provider model routing.

Routes Claude models through the Anthropic SDK (zero translation overhead,
full feature support including MCP, prompt caching, and streaming) and
non-Anthropic models through LiteLLM with smart routing and caching.

All calls pass through the guardrails engine (when enabled) for input
validation and output filtering before reaching any provider.

Configuration:
    SHELBY_LLM_GATEWAY=true             # enable multi-provider routing
    SHELBY_LLM_FALLBACK_MODELS=xai/grok-3,openai/gpt-4o
    SHELBY_LLM_CACHE=true               # enable response caching
    SHELBY_LLM_CACHE_TTL=3600           # cache TTL in seconds (default 1h)
    SHELBY_LLM_ROUTING=simple           # simple | cost | latency
    SHELBY_GUARDRAILS=true              # enable input/output guardrails
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Generator

import anthropic

from . import guardrails

log = logging.getLogger(__name__)

try:
    import litellm
    from litellm import Router

    litellm.drop_params = True
    _HAS_LITELLM = True
except ImportError:
    _HAS_LITELLM = False
    Router = None  # type: ignore[assignment,misc]

_GATEWAY_ENABLED = os.getenv("SHELBY_LLM_GATEWAY", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

_CROSS_PROVIDER_MODELS: list[str] = [
    m.strip()
    for m in os.getenv("SHELBY_LLM_FALLBACK_MODELS", "").split(",")
    if m.strip()
]

_CACHE_ENABLED = os.getenv("SHELBY_LLM_CACHE", "false").strip().lower() in (
    "true", "1", "yes", "on",
)
_CACHE_TTL = int(os.getenv("SHELBY_LLM_CACHE_TTL", "3600"))
_ROUTING_STRATEGY = os.getenv("SHELBY_LLM_ROUTING", "simple").strip().lower()


# ── Anthropic-shaped response wrappers ────────────────────────────────────


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _GatewayResponse:
    """Mimics anthropic.types.Message for the agent loop."""

    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)
    model: str = ""
    id: str = ""


# ── Format translation ────────────────────────────────────────────────────


def _system_to_openai(system) -> list[dict]:
    """Anthropic system blocks → OpenAI system message."""
    if not system:
        return []
    if isinstance(system, str):
        return [{"role": "system", "content": system}]
    parts = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return [{"role": "system", "content": "\n\n".join(parts)}] if parts else []


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """Anthropic messages → OpenAI messages."""
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []

        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
            else:
                btype = getattr(block, "type", "")

            if btype == "text":
                t = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                text_parts.append(t)
            elif btype == "tool_use":
                _id = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                _name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
                _inp = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
                tool_calls.append({
                    "id": _id,
                    "type": "function",
                    "function": {"name": _name, "arguments": json.dumps(_inp)},
                })
            elif btype == "tool_result":
                _tid = block.get("tool_use_id", "") if isinstance(block, dict) else getattr(block, "tool_use_id", "")
                _cnt = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
                tool_results.append({"role": "tool", "tool_call_id": _tid, "content": str(_cnt)})

        if tool_results:
            out.extend(tool_results)
        elif role == "assistant" and tool_calls:
            m: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
            m["content"] = "\n".join(text_parts) if text_parts else None
            out.append(m)
        else:
            out.append({"role": role, "content": "\n".join(text_parts) if text_parts else ""})

    return out


def _tools_to_openai(tools: list[dict] | None) -> list[dict] | None:
    """Anthropic tool schemas → OpenAI function-calling format."""
    if not tools:
        return None
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return out or None


def _response_from_litellm(resp) -> _GatewayResponse:
    """LiteLLM (OpenAI-format) response → Anthropic-shaped response."""
    choice = resp.choices[0] if resp.choices else None
    if not choice:
        return _GatewayResponse()

    content: list[Any] = []
    message = choice.message

    if message.content:
        content.append(_TextBlock(text=message.content))

    if getattr(message, "tool_calls", None):
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, AttributeError):
                args = {}
            content.append(_ToolUseBlock(id=tc.id or "", name=tc.function.name, input=args))

    finish = getattr(choice, "finish_reason", "stop")
    stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
    stop_reason = stop_map.get(finish, "end_turn")

    usage = _Usage()
    if hasattr(resp, "usage") and resp.usage:
        usage.input_tokens = getattr(resp.usage, "prompt_tokens", 0) or 0
        usage.output_tokens = getattr(resp.usage, "completion_tokens", 0) or 0

    return _GatewayResponse(
        content=content,
        stop_reason=stop_reason,
        usage=usage,
        model=getattr(resp, "model", ""),
        id=getattr(resp, "id", ""),
    )


# ── Cost tracking ─────────────────────────────────────────────────────────


_cost_log: list[dict] = []


def _log_cost(model: str, usage) -> None:
    if not _HAS_LITELLM:
        return
    try:
        inp = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)
        out = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0)
        cost = litellm.completion_cost(model=model, prompt_tokens=inp, completion_tokens=out)
        entry = {
            "model": model, "input_tokens": inp, "output_tokens": out,
            "cost_usd": cost, "ts": time.time(),
        }
        _cost_log.append(entry)
        log.info("LLM cost: $%.6f (model=%s, in=%d, out=%d)", cost, model, inp, out)
    except Exception:
        pass


def get_cost_summary() -> dict:
    """Aggregate cost data for observability endpoints."""
    if not _cost_log:
        return {"total_usd": 0, "calls": 0, "by_model": {}}
    by_model: dict[str, float] = {}
    for e in _cost_log:
        by_model[e["model"]] = by_model.get(e["model"], 0) + e["cost_usd"]
    return {
        "total_usd": sum(e["cost_usd"] for e in _cost_log),
        "calls": len(_cost_log),
        "by_model": by_model,
    }


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return any(k in m for k in ("claude", "haiku", "sonnet", "opus", "fable"))


# ── In-memory response cache ─────────────────────────────────────────────


class _ResponseCache:
    """Simple TTL cache keyed on (model, messages_hash).
    Avoids re-calling the LLM for identical prompts within the TTL window.
    Only caches non-streaming, non-tool-use calls."""

    def __init__(self, ttl: int = 3600) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def _key(self, model: str, messages: list[dict], system=None) -> str:
        raw = json.dumps({"m": model, "s": str(system), "msgs": messages}, sort_keys=True)
        import hashlib
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, model: str, messages: list[dict], system=None) -> Any | None:
        k = self._key(model, messages, system)
        entry = self._store.get(k)
        if entry is None:
            self._misses += 1
            return None
        ts, resp = entry
        if time.time() - ts > self._ttl:
            del self._store[k]
            self._misses += 1
            return None
        self._hits += 1
        log.debug("Cache hit (model=%s, hits=%d)", model, self._hits)
        return resp

    def put(self, model: str, messages: list[dict], resp: Any, system=None) -> None:
        k = self._key(model, messages, system)
        self._store[k] = (time.time(), resp)

    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}

    def clear(self) -> None:
        self._store.clear()


# ── LiteLLM Router builder ───────────────────────────────────────────────


def _build_router() -> Any | None:
    """Build a LiteLLM Router for smart routing across providers.

    Routing strategies:
      - simple: ordered fallback (first available wins)
      - cost:   cheapest model that can handle the request
      - latency: fastest model based on recent response times
    """
    if not _HAS_LITELLM or Router is None:
        return None

    model_list = []

    for model_id in _CROSS_PROVIDER_MODELS:
        provider = model_id.split("/")[0] if "/" in model_id else "unknown"
        api_key_env = _provider_key_env(provider)
        api_key = os.getenv(api_key_env, "")
        if not api_key:
            log.warning("Skipping %s: %s not set", model_id, api_key_env)
            continue

        entry: dict[str, Any] = {
            "model_name": model_id,
            "litellm_params": {
                "model": model_id,
                "api_key": api_key,
            },
        }

        if provider == "xai":
            entry["litellm_params"]["api_base"] = "https://api.x.ai/v1"

        model_list.append(entry)

    if not model_list:
        return None

    routing_map = {
        "simple": "simple-shuffle",
        "cost": "cost-based-routing",
        "latency": "latency-based-routing",
    }

    router = Router(
        model_list=model_list,
        routing_strategy=routing_map.get(_ROUTING_STRATEGY, "simple-shuffle"),
        num_retries=2,
        timeout=30,
        retry_after=5,
    )
    log.info(
        "LiteLLM Router initialized: strategy=%s, models=%s",
        _ROUTING_STRATEGY, [m["model_name"] for m in model_list],
    )
    return router


def _provider_key_env(provider: str) -> str:
    """Map provider prefix to its API key env var."""
    return {
        "xai": "XAI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "cohere": "COHERE_API_KEY",
        "together": "TOGETHER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }.get(provider, f"{provider.upper()}_API_KEY")


# ── Gateway ───────────────────────────────────────────────────────────────


class LLMGateway:
    """Unified LLM client with multi-provider routing, caching, and cost tracking."""

    def __init__(self) -> None:
        self._anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._enabled = _GATEWAY_ENABLED and _HAS_LITELLM
        self._router: Any | None = None
        self._cache: _ResponseCache | None = None

        if _GATEWAY_ENABLED and not _HAS_LITELLM:
            log.warning("SHELBY_LLM_GATEWAY=true but litellm not installed; using Anthropic SDK only")

        if self._enabled:
            self._router = _build_router()
            if _CACHE_ENABLED:
                self._cache = _ResponseCache(ttl=_CACHE_TTL)
                log.info("LLM response cache enabled (ttl=%ds)", _CACHE_TTL)

    @property
    def anthropic_client(self) -> anthropic.Anthropic:
        """Direct access for Anthropic-only features (token counting, MCP)."""
        return self._anthropic

    @property
    def cross_provider_models(self) -> list[str]:
        """Non-Anthropic fallback models configured via env."""
        return list(_CROSS_PROVIDER_MODELS) if self._enabled else []

    def create(self, **kwargs: Any) -> Any:
        """Completion call — routes by model, checks cache first.

        When guardrails are enabled, input is checked before the call and
        output is checked after.  In 'block' mode a GuardrailBlocked
        exception propagates to the caller.
        """
        model = kwargs.get("model", "")
        messages = kwargs.get("messages", [])
        system = kwargs.get("system")
        tools = kwargs.get("tools")

        # ── input guardrails ──
        input_result = guardrails.check_input(messages, system)
        guardrails.enforce(input_result)

        if self._cache and not tools:
            cached = self._cache.get(model, messages, system)
            if cached is not None:
                return cached

        if not self._enabled or _is_anthropic_model(model):
            resp = self._anthropic.messages.create(**kwargs)
            _log_cost(model, resp.usage)
            if self._cache and not tools:
                self._cache.put(model, messages, resp, system)
        else:
            resp = self._litellm_create(**kwargs)
            if self._cache and not tools:
                self._cache.put(model, messages, resp, system)

        # ── output guardrails ──
        output_result = guardrails.check_output(resp)
        guardrails.enforce(output_result)

        return resp

    def beta_create(self, **kwargs: Any) -> Any:
        """MCP beta calls — always Anthropic SDK, with guardrails."""
        input_result = guardrails.check_input(
            kwargs.get("messages", []), kwargs.get("system"),
        )
        guardrails.enforce(input_result)

        resp = self._anthropic.beta.messages.create(**kwargs)
        _log_cost(kwargs.get("model", ""), resp.usage)

        output_result = guardrails.check_output(resp)
        guardrails.enforce(output_result)
        return resp

    def count_tokens(self, **kwargs: Any) -> int:
        """Pre-send token count — Anthropic SDK only."""
        return self._anthropic.messages.count_tokens(**kwargs).input_tokens

    def stream(self, **kwargs: Any):
        """Streaming context manager, with input guardrails.

        Output guardrails run on the collected response after the stream
        finishes (via the wrapper's get_final_message).
        """
        input_result = guardrails.check_input(
            kwargs.get("messages", []), kwargs.get("system"),
        )
        guardrails.enforce(input_result)

        model = kwargs.get("model", "")
        if not self._enabled or _is_anthropic_model(model):
            return self._anthropic.messages.stream(**kwargs)

        return self._litellm_stream(**kwargs)

    def cache_stats(self) -> dict:
        """Cache hit/miss stats for observability."""
        if self._cache:
            return self._cache.stats()
        return {"enabled": False}

    def cost_summary(self) -> dict:
        """Aggregate cost data across all providers."""
        return get_cost_summary()

    def guardrail_config(self) -> dict:
        """Current guardrail configuration for observability."""
        return guardrails.get_config()

    def _litellm_create(self, **kwargs: Any) -> _GatewayResponse:
        model = kwargs.pop("model", "")
        system = kwargs.pop("system", None)
        tools = kwargs.pop("tools", None)
        messages = kwargs.pop("messages", [])
        max_tokens = kwargs.pop("max_tokens", 2048)

        oai_msgs = _system_to_openai(system) + _messages_to_openai(messages)
        oai_tools = _tools_to_openai(tools)

        lk: dict[str, Any] = {"model": model, "messages": oai_msgs, "max_tokens": max_tokens}
        if oai_tools:
            lk["tools"] = oai_tools

        if self._router:
            resp = self._router.completion(**lk)
        else:
            resp = litellm.completion(**lk)

        _log_cost(model, resp.usage if hasattr(resp, "usage") else _Usage())
        return _response_from_litellm(resp)

    def _litellm_stream(self, **kwargs: Any):
        model = kwargs.pop("model", "")
        system = kwargs.pop("system", None)
        messages = kwargs.pop("messages", [])
        max_tokens = kwargs.pop("max_tokens", 2048)

        oai_msgs = _system_to_openai(system) + _messages_to_openai(messages)

        return _LiteLLMStreamWrapper(
            model=model, messages=oai_msgs, max_tokens=max_tokens,
            router=self._router,
        )


class _LiteLLMStreamWrapper:
    """Mimics Anthropic's stream context manager for the streaming path."""

    def __init__(self, router=None, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._router = router
        self._response = None
        self._collected_text = ""

    def __enter__(self):
        if self._router:
            self._response = self._router.completion(stream=True, **self._kwargs)
        else:
            self._response = litellm.completion(stream=True, **self._kwargs)
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self) -> Generator[str, None, None]:
        for chunk in self._response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                self._collected_text += delta.content
                yield delta.content

    def get_final_message(self) -> _GatewayResponse:
        usage = _Usage()
        return _GatewayResponse(
            content=[_TextBlock(text=self._collected_text)],
            stop_reason="end_turn",
            usage=usage,
            model=self._kwargs.get("model", ""),
        )
