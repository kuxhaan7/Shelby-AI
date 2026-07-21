"""Core agent loop — Claude with tool use and RAG memory."""

from __future__ import annotations

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


class ShelbyAgent:
    """Stateless Claude agent with an agentic tool-use loop."""

    def __init__(self, rag_store: RagStore | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._rag = rag_store

    def chat(self, messages: list[dict], max_iterations: int = 6) -> str:
        """Run the full agentic loop and return the final text response."""
        msgs = list(messages)
        for _ in range(max_iterations):
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=msgs,
            )

            if response.stop_reason == "end_turn":
                return _extract_text(response)

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

            # Unexpected stop reason — return whatever text we have
            return _extract_text(response)

        return "Max iterations reached."

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


def _extract_text(response: Any) -> str:
    return "".join(b.text for b in response.content if hasattr(b, "text"))
