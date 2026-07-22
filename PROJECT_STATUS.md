# Shelby AI — Project Status & Audit

_Last updated: 2026-07-22_

Shelby is a production Claude-powered agent built to demonstrate Forward-Deployed
Engineer (FDE) capabilities: taking a real, messy problem and shipping an
autonomous agent that solves it end to end. It runs as a single deployed service
exposing both a **web chat UI** and a **Telegram bot**, backed by tool use, RAG,
persistent memory, a dynamic skill system, scheduling, and voice.

~2,860 lines of Python across a clean, modular package.

---

## 1. Architecture

```
FastAPI service (uvicorn)
├── Web chat UI          → GET /  (streaming markdown chat)
├── REST API             → /chat, /chat/stream, /rag/*, /health
├── Telegram bot         → runs in the same event loop (background task)
└── Task scheduler       → APScheduler heartbeat + cron jobs
        │
        ▼
   ShelbyAgent  (core agent loop)
   ├── Model fallback chain: sonnet-5 → sonnet-4-6 → haiku-4-5
   ├── Tool-use loop (Claude function calling)
   ├── Token usage tracking (per model, with $ cost)
   └── Tools ▼
        ├── web_search (Tavily)         ├── learn_skill / run_skill / list_skills
        ├── search_knowledge_base (RAG) ├── schedule_task / list_tasks / cancel_task
        ├── remember (RAG)              ├── write_memory / read_memory
        └── calculate / get_current_time
```

---

## 2. What's built (feature audit)

| # | Capability | Where | Status |
|---|-----------|-------|--------|
| 1 | **Tool use** — 13 tools via Claude function calling | `shelby/tools.py` | ✅ |
| 2 | **RAG memory** — ChromaDB, cosine HNSW, chunked ingest | `shelby/rag/` | ✅ |
| 3 | **FastAPI server** — REST + SSE streaming | `shelby/api/main.py` | ✅ |
| 4 | **LangChain evals** — LLM-as-judge (LCEL) | `shelby/evals/` | ✅ |
| 5 | **Web chat UI** — dark, streaming, markdown | `shelby/api/static/index.html` | ✅ |
| 6 | **Telegram bot** — text↔text, voice↔voice | `shelby/telegram/bot.py` | ✅ |
| 7 | **Voice out (TTS)** — ElevenLabs → OGG via ffmpeg | `shelby/tts/elevenlabs.py` | ✅ |
| 8 | **Voice in (STT)** — ElevenLabs Scribe | `shelby/stt/elevenlabs.py` | ✅ |
| 9 | **Self-writing memory** — JSON key-value store | `shelby/memory/notes.py` | ✅ |
| 10 | **Dynamic skills** — write/run Python skills at runtime | `shelby/skills/registry.py` | ✅ |
| 11 | **Web search** — real-time via Tavily | `shelby/tools.py` | ✅ |
| 12 | **Model fallback** — auto-drop on 429/500/503/529 | `shelby/agent.py` | ✅ |
| 13 | **Token usage tracking** — per-model tokens + est. cost | `shelby/usage_tracker.py` | ✅ |
| 14 | **Heartbeat + cron** — scheduled skill execution (30-min heartbeat) | `shelby/scheduler.py` | ✅ |
| 15 | **Data-quality FDE loop** — inspect → fix → evaluate broken data | `shelby/dataquality/` | ✅ |

---

## 3. Flagship capability: the Data-Quality FDE Loop

The strongest demo — the exact job a Palantir FDE does. Takes two broken
source-system exports (a CRM `customers` file and an ERP `orders` file that must
join) and drives the full loop:

1. **Inspect** (`profiler.py`) — diagnoses 7 defect classes: duplicate rows,
   nulls, inconsistent date formats, categorical casing/typos, broken
   referential integrity, messy numeric formatting, whitespace/case join-key noise.
2. **Fix** (`cleaner.py`) — applies Foundry-style transformations, **quarantines**
   unrecoverable orphaned rows (rather than silently dropping them), and returns a
   full changelog of every transformation.
3. **Evaluate** (`evaluate.py`) — objective before/after quality score across 6
   dimensions: completeness, uniqueness, validity, consistency, integrity, key hygiene.

**Result: 76.0 → 100.0** (+24 points), 29 unrecoverable rows quarantined.

Exposed as three agent skills — `inspect_dataset`, `fix_dataset`,
`evaluate_dataset` — so Shelby drives the whole loop conversationally.

Run the visual demo:
```bash
python -m shelby.dataquality.demo
```

---

## 4. Deployment

- **Platform:** Railway (project `endearing-solace` / `production`)
- **URL:** https://shelby-ai-production.up.railway.app
- **Start command:** `uvicorn shelby.api.main:app --host 0.0.0.0 --port $PORT`
- **Container:** multi-stage Dockerfile (builder + slim runtime with ffmpeg)
- **Both surfaces in one process:** FastAPI serves the web UI; the Telegram bot
  starts as a background task inside the FastAPI lifespan.

### Environment variables (set in Railway, never committed)
| Var | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | Claude API |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `ELEVENLABS_API_KEY` | TTS + STT |
| `TAVILY_API_KEY` | Web search |

---

## 5. Repo layout

```
shelby/
├── agent.py            core agent loop, model fallback, system prompt
├── tools.py            13 tools + dispatcher
├── scheduler.py        APScheduler heartbeat + cron jobs
├── usage_tracker.py    token/cost accounting
├── api/                FastAPI: main.py, models.py, static/index.html
├── dataquality/        FDE loop: generate_broken, profiler, cleaner, evaluate, demo
├── rag/                ChromaDB store + ingest
├── memory/             JSON key-value NotesStore
├── skills/             dynamic skill registry
├── telegram/           bot (text/voice)
├── tts/ · stt/         ElevenLabs voice
└── evals/              LangChain LLM-as-judge
data/skills/            bundled skills (usage stats, data-quality loop)
tests/                  tool, RAG, eval-structure tests
```

---

## 6. Known follow-ups

- Confirm Railway builds from the **Dockerfile** (dashboard showed a "Railpack"
  builder) so ffmpeg is present in the image for voice.
- Rotate any API keys/tokens that were ever shared in plaintext.
- Optional: expose the data-quality scorecard as a visual panel in the web UI.
