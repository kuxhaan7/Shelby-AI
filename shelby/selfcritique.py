"""Self-critique + self-improvement engine.

Shelby critiques its own answers, extracts a durable, generalisable lesson,
persists it, and produces an improved answer plus alternative angles. Lessons
are stored two ways:
  1. data/lessons.json  — full, timestamped history (the learning record)
  2. NotesStore key 'learned_lessons' — a compact digest that the Telegram/web
     layers inject into the start of future conversations, so past mistakes
     actually shape future answers.

The critique itself is done by Claude (LLM-as-critic). If the API is
unavailable, it degrades to a transparent heuristic critique rather than
fabricating a polished one.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from .paths import lessons_file

_LESSONS_FILE = lessons_file()
_CRITIC_MODEL = os.getenv("SHELBY_CRITIC_MODEL", "claude-sonnet-5")
_MAX_DIGEST = 12  # how many recent lessons to inject into conversation memory

_CRITIC_SYSTEM = """You are a rigorous self-critique engine for an AI agent named Shelby.
Given a question Shelby was asked, the answer it gave, and any user feedback,
critique the answer honestly and produce a better one. Be specific and unsparing —
the goal is genuine improvement, not reassurance.

Return ONLY a JSON object with exactly these keys:
{
  "rating": <integer 1-10, how good the original answer was>,
  "weaknesses": [<specific, concrete flaws — omit if none>],
  "lesson": "<one durable, GENERALISABLE rule Shelby should remember for next time; not specific to this exact question>",
  "improved_answer": "<a materially better answer to the original question>",
  "alternatives": [<other valid angles, approaches, or possibilities worth considering>]
}
No prose outside the JSON."""


# ── Lesson storage ────────────────────────────────────────────────────────────

class LessonStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _LESSONS_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []

    def add(self, lesson: str, question: str = "", rating: int | None = None) -> None:
        lessons = self.all()
        lessons.append({
            "lesson": lesson,
            "question": question[:300],
            "rating": rating,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self._path.write_text(json.dumps(lessons, indent=2))

    def recent(self, n: int = _MAX_DIGEST) -> list[dict]:
        return self.all()[-n:]

    def search(self, query: str, n: int = 5) -> list[dict]:
        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 3}
        scored = []
        for item in self.all():
            hay = (item["lesson"] + " " + item.get("question", "")).lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:n]]

    def digest(self) -> str:
        recent = self.recent()
        if not recent:
            return ""
        return "; ".join(f"({i+1}) {x['lesson']}" for i, x in enumerate(recent))


# ── LLM critic ────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _llm_critique(question: str, answer: str, feedback: str) -> dict | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from .llm_gateway import LLMGateway
        gateway = LLMGateway()
        user = (
            f"QUESTION:\n{question}\n\n"
            f"SHELBY'S ANSWER:\n{answer}\n\n"
            f"USER FEEDBACK (may be empty):\n{feedback or '(none)'}"
        )
        resp = gateway.create(
            model=_CRITIC_MODEL,
            max_tokens=1500,
            system=_CRITIC_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return _parse_json(text)
    except Exception as exc:
        log.warning("Self-critique LLM call failed: %s", exc)
        return None


def _heuristic_critique(question: str, answer: str, feedback: str) -> dict:
    weaknesses = []
    lower = answer.lower()
    if any(p in lower for p in ("i don't know", "i cannot", "as of my knowledge cutoff")):
        weaknesses.append("Gave up or cited a knowledge cutoff instead of using tools/web search.")
    if feedback:
        weaknesses.append(f"User feedback flagged an issue: {feedback[:200]}")
    if len(answer) < 40:
        weaknesses.append("Answer was thin — likely under-explained.")
    lesson = (
        f"When asked something like '{question[:80]}', verify with tools first and "
        "address the user's feedback directly."
        if feedback else
        "Prefer verified, tool-backed answers over assertions; be specific and complete."
    )
    return {
        "rating": 4 if weaknesses else 6,
        "weaknesses": weaknesses or ["(No LLM critic available — heuristic review only.)"],
        "lesson": lesson,
        "improved_answer": "(LLM critic unavailable — re-run with ANTHROPIC_API_KEY set for a full rewrite.)",
        "alternatives": [],
        "_heuristic": True,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def critique_and_learn(question: str, answer: str, feedback: str = "",
                       notes_store: Any = None) -> dict:
    """Critique a (question, answer) pair, store the lesson, return the critique.

    Passing the live *notes_store* lets the lesson digest take effect immediately;
    otherwise a fresh NotesStore is used (effective from the next conversation).
    """
    result = _llm_critique(question, answer, feedback) or _heuristic_critique(question, answer, feedback)

    lesson = (result.get("lesson") or "").strip()
    if lesson:
        store = LessonStore()
        store.add(lesson, question=question, rating=result.get("rating"))
        # Mirror a digest into conversation memory so it shapes future answers.
        try:
            from .memory.notes import NotesStore
            notes = notes_store or NotesStore()
            notes.write("learned_lessons", store.digest())
        except Exception as exc:
            log.warning("Could not write lesson digest to memory: %s", exc)

    return result


def recall_lessons(query: str, n: int = 5) -> list[dict]:
    return LessonStore().search(query, n)


def all_lessons() -> list[dict]:
    return LessonStore().all()
