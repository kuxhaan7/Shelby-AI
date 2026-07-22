# Bug Fix Report — 2026-07-22

**Session:** FDE role pitch — Shelby-AI eval loop  
**Branch merged:** `claude/bash-fde-role-pitch-hlqkl8` → `main`  
**Final commit:** `df5d750`  
**Test result:** 27/27 passed

---

## Bug 1 — `langchain.evaluation` module removed in LangChain 1.x

**File:** `shelby/evals/evaluators.py`  
**Severity:** Critical (import error — eval suite could not start at all)

### What broke
`langchain.evaluation.load_evaluator` was called to create `labeled_criteria`
and `criteria` evaluators. This API was removed when LangChain restructured
its evaluation tooling in the 1.x release series.

```python
# Before — raises ModuleNotFoundError on LangChain >= 1.0
from langchain.evaluation import load_evaluator

evaluator = load_evaluator("labeled_criteria", llm=_llm(), criteria={...})
result = evaluator.evaluate_strings(prediction=..., reference=..., input="")
```

### Root cause
`requirements.txt` pinned `langchain>=0.2.0` but the installed version
resolved to `1.3.14`, where `langchain.evaluation` no longer exists.

### Fix
Rewrote evaluators using LCEL (LangChain Expression Language) — the current
idiomatic pattern. Each evaluator is now a simple chain:

```
ChatPromptTemplate | ChatAnthropic | JsonOutputParser
```

The LLM-as-judge prompts ask Claude to return structured JSON
`{"score": 0.0–1.0, "reasoning": "..."}` for faithfulness, relevance,
and conciseness dimensions.

```python
# After — LangChain 1.x LCEL approach
from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

chain = _FAITHFULNESS_PROMPT | _llm() | JsonOutputParser()
result = chain.invoke({"context": context, "prediction": prediction})
```

**Added:** `SHELBY_EVAL_MODEL` env var (defaults to `claude-haiku-4-5-20251001`)
so eval runs use a fast/cheap model independently of the main agent model.

---

## Bug 2 — Wrong assertion in `test_chunk_splits_long_text`

**File:** `tests/test_rag.py`  
**Severity:** Low (test bug, not production code)

### What broke
Test asserted that the second chunk of a 600-word text chunked at size=512
would also be 512 words long:

```python
assert len(chunks[1].split()) == 512  # FAILS — actual: 152
```

### Root cause
With 600 words, size=512, overlap=64:
- Chunk 1 starts at word 0 → ends at word 512 (512 words) ✓
- Chunk 2 starts at word 448 (512−64) → ends at word 600 (152 words) ✓

The tail chunk is always `≤ size`, not exactly `size`. The test expectation
was incorrect.

### Fix
```python
# After
assert len(chunks[0].split()) == 512   # first chunk is full size
assert len(chunks[-1].split()) <= 512  # last chunk is the remainder
```

---

## Test suite added

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_tools.py` | 11 | Tool schemas, dispatch, calculate edge cases, error handling |
| `tests/test_rag.py` | 12 | Chunker, RagStore add/batch/query/count, ingest_text/file |
| `tests/test_evals_structure.py` | 4 | Dataset completeness, prompt variables, function callability |
| **Total** | **27** | **27/27 passing** |

All tests run without `ANTHROPIC_API_KEY` — only the live LLM eval runner
(`python -m shelby.evals.run`) requires a key.

---

## Bug 3 — `ChatPromptTemplate` parses literal `{score}` as a missing template variable

**File:** `shelby/evals/evaluators.py`  
**Severity:** High (all eval scores returned `None` — evaluators silently failed on every sample)

### What broke
When the live eval runner was first executed with a real API key, every score
came back `N/A` and the error message was:

```
Input to ChatPromptTemplate is missing variables {'"score"'}
```

The `_SCHEMA` string used single curly braces:

```python
_SCHEMA = '{"score": <0.0-1.0>, "reasoning": "<one sentence>"}'
```

LangChain's `ChatPromptTemplate` treats any `{...}` inside a string literal as
a template variable and raises `KeyError` / `ValidationError` at invoke time
when the variable is not supplied.

### Root cause
Python f-string / Jinja2-style escaping: single `{` → variable placeholder,
double `{{` → literal `{`. The prompt string was injected into the template
via string concatenation, so it was still subject to template-variable parsing.

### Fix

```python
# Before — LangChain sees {score} and {reasoning} as missing variables
_SCHEMA = '{"score": <0.0-1.0>, "reasoning": "<one sentence>"}'

# After — double braces produce literal { } in the rendered prompt
_SCHEMA = '{{"score": <0.0-1.0>, "reasoning": "<one sentence>"}}'
```

---

## Final eval results (post all three fixes)

Command run: `python -m shelby.evals.run`  
Model: `claude-haiku-4-5-20251001` (via `SHELBY_EVAL_MODEL`)

| # | Question (abbrev.) | Faithfulness | Relevance |
|---|-------------------|:------------:|:---------:|
| 1 | What is Shelby? | 1.00 ✓ | 0.85 |
| 2 | How does memory work? | 1.00 ✓ | 0.85 |
| 3 | What tools does Shelby have? | 1.00 ✓ | 1.00 ✓ |
| 4 | What API does Shelby expose? | 1.00 ✓ | 0.85 |
| **Avg** | | **1.00** | **0.89** |

Faithfulness: **100% across all samples** — no hallucinations detected.  
Relevance: **89% average** — 3 of 4 questions scored 0.85 (answers were
correct but the judge noted they could be more directly focused on the
question).

---

## Known blocker (not a bug)

`python -m shelby.evals.run` calls Claude via the Anthropic SDK.
Requires `ANTHROPIC_API_KEY` set in the environment.  
Action: add key in `claude.ai/settings` → environment variables.
