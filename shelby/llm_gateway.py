"""LLM Gateway — unified interface for multi-provider model routing.

Routes Claude models through the Anthropic SDK (zero translation overhead,
full feature support including MCP, prompt caching, and streaming) and
non-Anthropic models through LiteLLM.

Configuration:
    SHELBY_LLM_GATEWAY=true             # enable LiteLLM for non-Anthropic models
    SHELBY_LLM_FALLBACK_MODELS=openai/gpt-4o,gemini/gemini-2.0-flash
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Generator

import anthropic

log = logging.getLogger(__name__)

try:
    import litellm

    litellm.drop_params = True
    _HAS_LITELLM = True
except ImportError:
    _HAS_LITELLM = False

_GATEWAY_ENABLED = os.getenv("SHELBY_LLM_GATEWAY", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

_CROSS_PROVIDER_MODELS: list[str] = [
    m.strip()
    for m in os.getenv("SHELBY_LLM_FALLBACK_MODELS", "").split(",")
    if m.strip()
]


# ── Anthropic-shaped response wrappers ────────────────────────────────────
# The agent loop expects Anthropic response objects. These lightweight
# dataclasses let LiteLLM responses drop in without touching the loop.


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


def _log_cost(model: str, usage) -> None:
    if not _HAS_LITELLM:
        return
    try:
        inp = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)
        out = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0)
        cost = litellm.completion_cost(model=model, prompt_tokens=inp, completion_tokens=out)
        log.info("LLM cost: $%.6f (model=%s, in=%d, out=%d)", cost, model, inp, out)
    except Exception:
        pass


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return any(k in m for k in ("claude", "haiku", "sonnet", "opus", "fable"))


# ── Gateway ───────────────────────────────────────────────────────────────


class LLMGateway:
    """Unified LLM client. Claude models → Anthropic SDK; others → LiteLLM."""

    def __init__(self) -> None:
        self._anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._enabled = _GATEWAY_ENABLED and _HAS_LITELLM
        if _GATEWAY_ENABLED and not _HAS_LITELLM:
            log.warning("SHELBY_LLM_GATEWAY=true but litellm not installed; using Anthropic SDK only")

    @property
    def anthropic_client(self) -> anthropic.Anthropic:
        """Direct access for Anthropic-only features (token counting, MCP)."""
        return self._anthropic

    @property
    def cross_provider_models(self) -> list[str]:
        """Non-Anthropic fallback models configured via env."""
        return list(_CROSS_PROVIDER_MODELS) if self._enabled else []

    def create(self, **kwargs: Any) -> Any:
        """Completion call — routes by model."""
        model = kwargs.get("model", "")

        if not self._enabled or _is_anthropic_model(model):
            resp = self._anthropic.messages.create(**kwargs)
            _log_cost(model, resp.usage)
            return resp

        return self._litellm_create(**kwargs)

    def beta_create(self, **kwargs: Any) -> Any:
        """MCP beta calls — always Anthropic SDK."""
        resp = self._anthropic.beta.messages.create(**kwargs)
        _log_cost(kwargs.get("model", ""), resp.usage)
        return resp

    def count_tokens(self, **kwargs: Any) -> int:
        """Pre-send token count — Anthropic SDK only."""
        return self._anthropic.messages.count_tokens(**kwargs).input_tokens

    def stream(self, **kwargs: Any):
        """Streaming context manager — Anthropic SDK for Claude models,
        LiteLLM text generator for others."""
        model = kwargs.get("model", "")

        if not self._enabled or _is_anthropic_model(model):
            return self._anthropic.messages.stream(**kwargs)

        return self._litellm_stream(**kwargs)

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

        resp = litellm.completion(**lk)
        _log_cost(model, resp.usage if hasattr(resp, "usage") else _Usage())
        return _response_from_litellm(resp)

    def _litellm_stream(self, **kwargs: Any):
        """Returns a context-manager-like object that yields text chunks."""
        model = kwargs.pop("model", "")
        system = kwargs.pop("system", None)
        messages = kwargs.pop("messages", [])
        max_tokens = kwargs.pop("max_tokens", 2048)

        oai_msgs = _system_to_openai(system) + _messages_to_openai(messages)

        return _LiteLLMStreamWrapper(
            model=model, messages=oai_msgs, max_tokens=max_tokens,
        )


class _LiteLLMStreamWrapper:
    """Mimics Anthropic's stream context manager for the streaming path."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._response = None
        self._collected_text = ""

    def __enter__(self):
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
