"""Core agent loop — Claude with tool use and RAG memory."""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from typing import Any

import anthropic

from .rag.store import RagStore
from .tools import TOOL_SCHEMAS, dispatch

MODEL = os.getenv("SHELBY_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """You are Shelby, a sharp and resourceful AI assistant.
You have access to a persistent knowledge base (RAG) and can use tools to answer questions.
Be concise. Use tools when they add value. Search your knowledge base before stating you don't know something."""

log = logging.getLogger(__name__)


class TokenUsage:
    """Accumulated token usage across all turns in a single chat() call."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def log(self) -> None:
        log.info(
            "tokens — in: %d  out: %d  cache_read: %d  cache_write: %d  total: %d",
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.total_tokens,
        )

    def summary(self) -> str:
        return (
            f"in={self.input_tokens} out={self.output_tokens} "
            f"cache_read={self.cache_read_tokens} total={self.total_tokens}"
        )


class ShelbyAgent:
    """Stateless Claude agent with an agentic tool-use loop."""

    def __init__(self, rag_store: RagStore | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._rag = rag_store

    def chat(self, messages: list[dict], max_iterations: int = 6) -> str:
        """Run the full agentic loop and return the final text response."""
        text, _ = self.chat_with_usage(messages, max_iterations)
        return text

    def chat_with_usage(
        self, messages: list[dict], max_iterations: int = 6
    ) -> tuple[str, TokenUsage]:
        """Like chat() but also returns accumulated TokenUsage."""
        msgs = list(messages)
        usage = TokenUsage()

        for _ in range(max_iterations):
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=msgs,
            )
            usage.add(response.usage)

            if response.stop_reason == "end_turn":
                usage.log()
                return _extract_text(response), usage

            if response.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = dispatch(block.name, block.input, self._rag)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })
                msgs.append({"role": "user", "content": tool_results})
                continue

            usage.log()
            return _extract_text(response), usage

        usage.log()
        return "Max iterations reached.", usage

    def stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream the final response (no tool calls in streaming path for simplicity)."""
        with self._client.messages.stream(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
            usage = TokenUsage()
            usage.add(final.usage)
            usage.log()


def _extract_text(response: Any) -> str:
    return "".join(b.text for b in response.content if hasattr(b, "text"))
