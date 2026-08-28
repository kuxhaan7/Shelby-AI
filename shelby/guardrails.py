"""Guardrails engine — input/output safety for all LLM calls.

Applies pre-call (input) and post-call (output) checks regardless of which
provider handles the request.  Works at the SDK level, not the proxy level,
so it wraps LLMGateway.create / .stream transparently.

Checks
------
1. **Input length** — rejects prompts exceeding a configurable character limit.
2. **PII detection / masking** — regex-based (SSN, credit-card, email, phone,
   IP address).  Masks on input, optionally masks on output.
3. **Prompt-injection heuristics** — pattern library that catches common
   injection prefixes, role-override attempts, and encoded payloads.
4. **Toxicity keywords** — lightweight keyword scan for clearly harmful
   content.  Not a classifier — catches only obvious signals.

Configuration (env vars)
------------------------
SHELBY_GUARDRAILS           = true           # master switch
SHELBY_GUARDRAILS_PII       = true           # PII detection
SHELBY_GUARDRAILS_INJECTION = true           # prompt injection detection
SHELBY_GUARDRAILS_TOXICITY  = true           # toxic keyword scan
SHELBY_GUARDRAILS_MAX_INPUT = 50000          # max input chars (0 = unlimited)
SHELBY_GUARDRAILS_MODE      = block          # block | warn | log
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_truthy = {"true", "1", "yes", "on"}

_ENABLED = os.getenv("SHELBY_GUARDRAILS", "false").strip().lower() in _truthy
_PII_ENABLED = os.getenv("SHELBY_GUARDRAILS_PII", "true").strip().lower() in _truthy
_INJECTION_ENABLED = os.getenv("SHELBY_GUARDRAILS_INJECTION", "true").strip().lower() in _truthy
_TOXICITY_ENABLED = os.getenv("SHELBY_GUARDRAILS_TOXICITY", "true").strip().lower() in _truthy
_MAX_INPUT = int(os.getenv("SHELBY_GUARDRAILS_MAX_INPUT", "50000"))
_MODE = os.getenv("SHELBY_GUARDRAILS_MODE", "block").strip().lower()  # block | warn | log


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class Violation:
    category: str          # pii | injection | toxicity | length
    detail: str
    span: tuple[int, int] | None = None  # character offsets, if applicable


@dataclass
class GuardrailResult:
    passed: bool = True
    violations: list[Violation] = field(default_factory=list)
    masked_text: str | None = None  # input with PII replaced (if masking is on)

    def add(self, category: str, detail: str, span: tuple[int, int] | None = None) -> None:
        self.violations.append(Violation(category=category, detail=detail, span=span))
        self.passed = False


class GuardrailBlocked(Exception):
    """Raised in 'block' mode when a violation is detected."""
    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        descs = "; ".join(v.detail for v in result.violations[:3])
        super().__init__(f"Guardrail blocked: {descs}")


# ── PII patterns ─────────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(
        r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
        r"[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,4}\b"
    )),
    ("email", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    )),
    ("phone_us", re.compile(
        r"\b(?:\+1[\s-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"
    )),
    ("ip_address", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    )),
    ("us_passport", re.compile(r"\b[A-Z]\d{8}\b")),
    ("date_of_birth", re.compile(
        r"\b(?:0[1-9]|1[0-2])[/-](?:0[1-9]|[12]\d|3[01])[/-]"
        r"(?:19|20)\d{2}\b"
    )),
]

_PII_MASK = "<[PII_REDACTED]>"


def _check_pii(text: str) -> tuple[list[Violation], str]:
    violations: list[Violation] = []
    masked = text
    for label, pat in _PII_PATTERNS:
        for match in pat.finditer(text):
            violations.append(Violation(
                category="pii",
                detail=f"Detected {label}: {match.group()[:4]}***",
                span=(match.start(), match.end()),
            ))
        masked = pat.sub(_PII_MASK, masked)
    return violations, masked


# ── Prompt-injection detection ───────────────────────────────────────────────

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("role_override", re.compile(
        r"(?i)\b(?:ignore|disregard|forget)\b.{0,30}"
        r"(?:previous|above|prior|all)\b.{0,30}"
        r"(?:instructions?|rules?|prompt|guidelines?)",
        re.DOTALL,
    )),
    ("system_prompt_leak", re.compile(
        r"(?i)(?:reveal|show|print|output|repeat|display)\b.{0,30}"
        r"(?:system\s*prompt|initial\s*prompt|hidden\s*instructions?|"
        r"system\s*message|secret\s*instructions?)",
        re.DOTALL,
    )),
    ("role_injection", re.compile(
        r"(?i)\bYou\s+are\s+now\b.{0,60}"
        r"(?:DAN|jailbreak|unrestricted|unfiltered|evil|hack)",
        re.DOTALL,
    )),
    ("delimiter_injection", re.compile(
        r"(?:<\|(?:im_start|system|endoftext)\|>|"
        r"\[INST\]|\[\/INST\]|<<SYS>>|<\/s>)",
    )),
    ("encoded_payload", re.compile(
        r"(?i)(?:base64|eval|exec)\s*\(\s*['\"]"
    )),
]


def _check_injection(text: str) -> list[Violation]:
    violations: list[Violation] = []
    for label, pat in _INJECTION_PATTERNS:
        for match in pat.finditer(text):
            violations.append(Violation(
                category="injection",
                detail=f"Prompt injection pattern ({label})",
                span=(match.start(), match.end()),
            ))
    return violations


# ── Toxicity keyword scan ────────────────────────────────────────────────────

_TOXICITY_PHRASES: list[str] = [
    "how to make a bomb",
    "how to make explosives",
    "how to synthesize drugs",
    "how to hack into",
    "how to steal identity",
    "how to commit fraud",
    "create a virus",
    "create malware",
    "bypass security",
    "social engineering attack",
]

_TOXICITY_RE = re.compile(
    "|".join(re.escape(p) for p in _TOXICITY_PHRASES),
    re.IGNORECASE,
)


def _check_toxicity(text: str) -> list[Violation]:
    violations: list[Violation] = []
    for match in _TOXICITY_RE.finditer(text):
        violations.append(Violation(
            category="toxicity",
            detail=f"Potentially harmful content detected",
            span=(match.start(), match.end()),
        ))
    return violations


# ── Length check ─────────────────────────────────────────────────────────────

def _check_length(text: str) -> list[Violation]:
    if _MAX_INPUT and len(text) > _MAX_INPUT:
        return [Violation(
            category="length",
            detail=f"Input too long ({len(text):,} chars, max {_MAX_INPUT:,})",
        )]
    return []


# ── Text extraction ──────────────────────────────────────────────────────────

def _extract_text(messages: list[dict], system: Any = None) -> str:
    """Pull all user-visible text from an Anthropic-shaped message list."""
    parts: list[str] = []

    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
    return "\n".join(parts)


def _extract_response_text(response: Any) -> str:
    """Pull text from an Anthropic-shaped response."""
    parts: list[str] = []
    content = getattr(response, "content", [])
    if not isinstance(content, list):
        return str(content) if content else ""
    for block in content:
        text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
        if text:
            parts.append(text)
    return "\n".join(parts)


# ── Public API ───────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return _ENABLED


def check_input(messages: list[dict], system: Any = None) -> GuardrailResult:
    """Run all enabled input guardrails.  Returns a result with violations."""
    result = GuardrailResult()
    if not _ENABLED:
        return result

    text = _extract_text(messages, system)

    violations: list[Violation] = []

    violations.extend(_check_length(text))

    if _PII_ENABLED:
        pii_violations, masked = _check_pii(text)
        violations.extend(pii_violations)
        if pii_violations:
            result.masked_text = masked

    if _INJECTION_ENABLED:
        violations.extend(_check_injection(text))

    if _TOXICITY_ENABLED:
        violations.extend(_check_toxicity(text))

    for v in violations:
        result.add(v.category, v.detail, v.span)

    if violations:
        log.warning(
            "Guardrail violations (%d): %s",
            len(violations),
            ", ".join(f"{v.category}: {v.detail}" for v in violations[:5]),
        )

    return result


def check_output(response: Any) -> GuardrailResult:
    """Run output guardrails on the model response."""
    result = GuardrailResult()
    if not _ENABLED:
        return result

    text = _extract_response_text(response)
    if not text:
        return result

    if _PII_ENABLED:
        pii_violations, masked = _check_pii(text)
        for v in pii_violations:
            result.add(v.category, v.detail, v.span)
        if pii_violations:
            result.masked_text = masked

    return result


def enforce(result: GuardrailResult) -> None:
    """Apply the configured enforcement mode.

    - block:  raise GuardrailBlocked
    - warn:   log at WARNING (caller proceeds)
    - log:    log at INFO (caller proceeds)
    """
    if result.passed:
        return

    if _MODE == "block":
        raise GuardrailBlocked(result)
    elif _MODE == "warn":
        log.warning("Guardrail warning (not blocking): %s",
                     "; ".join(v.detail for v in result.violations[:5]))
    else:
        log.info("Guardrail log-only: %s",
                  "; ".join(v.detail for v in result.violations[:5]))


def get_config() -> dict:
    """Current guardrail configuration for observability endpoints."""
    return {
        "enabled": _ENABLED,
        "mode": _MODE,
        "checks": {
            "pii": _PII_ENABLED,
            "injection": _INJECTION_ENABLED,
            "toxicity": _TOXICITY_ENABLED,
            "max_input_chars": _MAX_INPUT,
        },
        "pii_patterns": [label for label, _ in _PII_PATTERNS],
        "injection_patterns": [label for label, _ in _INJECTION_PATTERNS],
    }
