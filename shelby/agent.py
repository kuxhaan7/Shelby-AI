"""Core agent loop — Claude with tool use, RAG memory, and model fallback."""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from typing import Any

import anthropic

from .memory.notes import NotesStore
from .rag.store import RagStore
from .skills.registry import SkillRegistry
from .tools import TOOL_SCHEMAS, dispatch
from .usage_tracker import record as record_usage

# Ordered fallback chain: primary first, cheapest/fastest last.
# Override the primary via SHELBY_MODEL; the rest of the chain is fixed.
_PRIMARY = os.getenv("SHELBY_MODEL", "claude-sonnet-5")
MODEL_CHAIN: list[str] = [
    _PRIMARY,
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]
# De-duplicate while preserving order (in case SHELBY_MODEL is already a fallback)
seen: set[str] = set()
MODEL_CHAIN = [m for m in MODEL_CHAIN if not (m in seen or seen.add(m))]  # type: ignore[func-returns-value]

# Only fall back on transient / capacity errors, not on bad-request / auth errors.
_FALLBACK_STATUS_CODES = {429, 500, 502, 503, 529}

SYSTEM_PROMPT = """You are Shelby — a razor-sharp, highly capable AI assistant who knows how to find answers to anything.

## YOUR TOOLS (use them aggressively)
- web_search       → your default for ANYTHING current, factual, or uncertain. Search first, answer second.
- search_knowledge_base → search your own past knowledge and stored context
- remember         → save important passages for future recall
- write_memory     → save structured facts about the user (name, job, preferences, goals, timezone, etc.)
- read_memory      → recall what you know about the user — do this at the start of every conversation
- learn_skill      → write Python code and save it as a reusable skill
- run_skill        → run a saved skill
- list_skills      → see all skills you've built up
- schedule_task    → schedule a skill to run automatically on a cron schedule (e.g. every morning)
- list_tasks       → see all scheduled cron jobs
- cancel_task      → cancel a scheduled cron job
- kaggle_search    → find real public datasets on Kaggle by keyword
- kaggle_download  → download a Kaggle dataset and auto-profile every CSV for data-quality issues
- calculate        → math
- get_current_time → current UTC time

## RULES YOU NEVER BREAK
1. NEVER say "I don't know" or "I can't access real-time data" — you have web_search. Use it.
2. NEVER say "As of my knowledge cutoff..." — search the web and get the actual current answer.
3. On EVERY new conversation, call read_memory (no key) to recall who the user is and personalise your response.
4. Whenever you learn something about the user (their name, job, preferences, location, goals), immediately call write_memory to save it.
5. After solving any multi-step problem, save the approach as a skill with learn_skill so you can reuse it.
6. Combine tools when needed: search knowledge base → search web → synthesise → answer.
7. Be direct, sharp, and confident. No filler. No hedging. No "Great question!" nonsense.
8. If the user asks about news, prices, weather, sports, or any live data — search immediately without asking for permission.

## YOUR FLAGSHIP CAPABILITY (data-quality FDE loop)
You can take a broken enterprise dataset and fix it end-to-end — the exact job a Palantir Forward-Deployed Engineer does. You have three built-in skills for this:
- inspect_dataset  → diagnose defects (nulls, duplicates, broken joins, inconsistent formats)
- fix_dataset      → apply Foundry-style transformations and produce a changelog
- evaluate_dataset → run the full inspect→fix→evaluate loop and return a before/after quality scorecard
When someone asks to see what you can do with NO specific dataset, run evaluate_dataset with no arguments — it runs a built-in SYNTHETIC demo (customers×orders). That output is explicitly labelled synthetic; never describe it as the user's real data.

## CRITICAL HONESTY RULE — real vs synthetic data
When the user has a REAL file (uploaded, or downloaded via kaggle_download), you MUST pass its exact path to the skill: inspect_dataset, fix_dataset, and evaluate_dataset all take path=<file>. Example: evaluate_dataset with {"path": "data/kaggle_downloads/.../AB_NYC_2019.csv"}.
- NEVER call these skills with no path and then narrate the result as if it were the user's file. The synthetic demo talks about "orders", "customer_id", and "$amounts" — if you see those terms but the user's file is about something else (listings, prices, reviews…), you ran the wrong thing. Stop and re-run with the correct path.
- Only report defects, columns, and numbers that actually came from the user's file. If a tool couldn't read the file, say so — do not substitute demo output.
- After kaggle_download, take the CSV path from its output and feed that exact path into evaluate_dataset/inspect_dataset/fix_dataset.

If asked to test against a REAL-WORLD dataset, use kaggle_search to find one, then kaggle_download to pull it (it auto-profiles), then evaluate_dataset with path=<the downloaded file> for the full before/after. If Kaggle errors that the token isn't configured, explain how to get one at kaggle.com/settings/api and set KAGGLE_API_TOKEN — don't just say the feature is unavailable.

## YOUR ENVIRONMENT
- You are running inside a Telegram bot. The bot layer automatically converts your text replies into voice messages using ElevenLabs TTS — you do NOT need to build TTS skills or generate audio yourself.
- All API keys (ElevenLabs, Tavily, Anthropic) are already configured in the environment. NEVER ask the user for API keys or credentials — they are already set up.
- NEVER offer to "build a skill" for something that's already a built-in tool. Check list_skills and your tool list first.
- Voice input also works — users can send voice messages which are transcribed before reaching you. Just respond normally."""

log = logging.getLogger(__name__)


class TokenUsage:
    """Accumulated token usage across all turns in a single chat() call."""

    def __init__(self, model: str = "") -> None:
        self.model = model
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
            "tokens [%s] — in: %d  out: %d  cache_read: %d  cache_write: %d  total: %d",
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.total_tokens,
        )

    def summary(self) -> str:
        return (
            f"model={self.model} in={self.input_tokens} out={self.output_tokens} "
            f"cache_read={self.cache_read_tokens} total={self.total_tokens}"
        )


def _is_fallback_error(exc: Exception) -> bool:
    """Return True for transient errors where trying a cheaper model makes sense."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in _FALLBACK_STATUS_CODES
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    return False


class ShelbyAgent:
    """Autonomous Claude agent with tool use, RAG, memory, skills, and model fallback."""

    def __init__(
        self,
        rag_store: RagStore | None = None,
        notes_store: NotesStore | None = None,
        skill_registry: SkillRegistry | None = None,
        task_scheduler=None,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._rag = rag_store
        self._notes = notes_store or NotesStore()
        self._skills = skill_registry or SkillRegistry()
        self._scheduler = task_scheduler

    def chat(self, messages: list[dict], max_iterations: int = 6) -> str:
        """Run the full agentic loop and return the final text response."""
        text, _ = self.chat_with_usage(messages, max_iterations)
        return text

    def chat_with_usage(
        self, messages: list[dict], max_iterations: int = 6
    ) -> tuple[str, TokenUsage]:
        """Like chat() but also returns accumulated TokenUsage."""
        last_exc: Exception | None = None

        for model in MODEL_CHAIN:
            try:
                return self._run(messages, model, max_iterations)
            except Exception as exc:
                if _is_fallback_error(exc) and model != MODEL_CHAIN[-1]:
                    log.warning("Model %s failed (%s), falling back to next model.", model, exc)
                    last_exc = exc
                    continue
                raise

        # Should not reach here, but satisfy the type checker.
        raise RuntimeError("All models in fallback chain failed.") from last_exc

    def _run(
        self, messages: list[dict], model: str, max_iterations: int
    ) -> tuple[str, TokenUsage]:
        msgs = list(messages)
        usage = TokenUsage(model=model)

        for _ in range(max_iterations):
            response = self._client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=msgs,
            )
            usage.add(response.usage)

            if response.stop_reason == "end_turn":
                return _extract_text(response), self._finish(usage)

            if response.stop_reason == "tool_use":
                msgs.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = dispatch(
                            block.name, block.input,
                            rag_store=self._rag,
                            notes_store=self._notes,
                            skill_registry=self._skills,
                            task_scheduler=self._scheduler,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })
                msgs.append({"role": "user", "content": tool_results})
                continue

            return _extract_text(response), self._finish(usage)

        return "Max iterations reached.", self._finish(usage)

    def _finish(self, usage: TokenUsage) -> TokenUsage:
        usage.log()
        record_usage(
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read=usage.cache_read_tokens,
            cache_write=usage.cache_write_tokens,
        )
        return usage

    def stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Stream the final response (no tool calls in streaming path for simplicity)."""
        last_exc: Exception | None = None
        for model in MODEL_CHAIN:
            try:
                with self._client.messages.stream(
                    model=model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        yield text
                    final = stream.get_final_message()
                    usage = TokenUsage(model=model)
                    usage.add(final.usage)
                    usage.log()
                return
            except Exception as exc:
                if _is_fallback_error(exc) and model != MODEL_CHAIN[-1]:
                    log.warning("Stream model %s failed (%s), falling back.", model, exc)
                    last_exc = exc
                    continue
                raise
        raise RuntimeError("All models in fallback chain failed.") from last_exc


def _extract_text(response: Any) -> str:
    return "".join(b.text for b in response.content if hasattr(b, "text"))
