# Shelby AI — Project Status, Audit & Journey Log

_A living record. Updated as the project evolves._
_Last updated: 2026-07-22_

Shelby is a production Claude-powered agent built to demonstrate Forward-Deployed
Engineer (FDE) capabilities: taking a real, messy problem and shipping an
autonomous agent that solves it end to end. It runs as a single deployed service
exposing both a **web chat UI** and a **Telegram bot**, backed by tool use, RAG,
persistent memory, a dynamic skill system, scheduling, and voice.

~2,860 lines of Python across a clean, modular package.

---

## 1. The Journey (narrative)

Shelby did not start as an AI project at all. It began as an **empty OpenClaw
config repository** — a pile of bash runtime files, gateway JSON, and agent
scaffolding with no real product inside. The goal was set early and never moved:

> _"Make it completely as we claim — tool use, python, rag, fastapi, langchain
> evals — ready for an FDE role pitch."_

From there it grew in deliberate phases, each one adding a capability that made
Shelby less of a chatbot and more of an autonomous engineer:

1. **Foundation** — strip the OpenClaw runtime, build a real Python agent with
   Claude tool use, RAG, and a FastAPI server.
2. **Proof** — wire in LangChain LLM-as-judge evals and loop until Shelby passed.
3. **Reach** — put Shelby in the user's pocket via Telegram, then give it a
   voice (ElevenLabs TTS + STT) with strict modality matching.
4. **Autonomy** — make Shelby better than the framework it replaced: web search,
   self-writing memory, a dynamic skill system it can extend at runtime.
5. **Reliability** — model fallback chain, token/cost tracking, a heartbeat loop
   and cron scheduling so Shelby can act on its own schedule.
6. **The real problem** — after rejecting a throwaway Kaggle ML demo, we landed
   on the authentic FDE story: **inspect, fix, and evaluate broken enterprise
   data** — the actual job, proven with a before/after quality score.
7. **Showcase** — a public web UI so hiring managers can try Shelby live, plus
   this audit as the single source of truth.

The throughline: every addition had to earn its place by making Shelby more
useful to a real person solving a real problem — never features for their own sake.

---

## 2. Milestones

| Phase | Milestone | Outcome |
|-------|-----------|---------|
| Foundation | Empty OpenClaw repo → real Python agent | 60 runtime files stripped; agent loop live |
| Foundation | Tool use, RAG, FastAPI shipped | Core stack working end to end |
| Proof | LangChain evals pass | LLM-as-judge harness green |
| Reach | Deployed to Railway | Public service running |
| Reach | Telegram bot connected | Talk to Shelby from your phone |
| Reach | Voice in + out (ElevenLabs) | Modality-matched: text↔text, voice↔voice |
| Autonomy | Web search (Tavily) | No more "I don't know" |
| Autonomy | Self-writing memory + dynamic skills | Shelby learns and remembers |
| Reliability | Model fallback chain | Survives rate limits / outages |
| Reliability | Token usage + cost tracking | Every call accounted for |
| Reliability | Heartbeat + cron scheduling | Shelby runs on its own schedule |
| Flagship | Data-quality FDE loop | **76.0 → 100.0** on broken data |
| Showcase | Public web chat UI | Hiring managers can demo it live |
| Showcase | Animated sci-fi emblem + glassmorphism redesign | 2026 AI-UI patterns; custom "sentinel" sigil animation |
| Showcase | In-UI data-quality scorecard | One-click demo renders animated before/after bars |
| Showcase | 90s-terminal theme + file & voice inputs | Scientific mono type, neon bg, Motion; upload CSV + speak |
| Real-world data | Kaggle search + download tools | Key-only setup; Shelby finds & profiles real datasets itself |
| Self-improvement | Self-critique skill (self_improve) | LLM-as-critic; learns durable lessons, applies them to future answers |
| File delivery | send_file tool + /download endpoint | Sends files as Telegram documents / web download chips (path-safe) |
| External services | Remote MCP connector | Shelby uses hosted MCP servers (Apollo, Gmail, Calendar, …) — env-configured, no secrets in repo |
| External services | Connect-by-URL at runtime | Paste an MCP link in chat; Shelby's connect_mcp tool registers it (persisted), just like adding a connector in Claude |

---

## 3. Hurdles & How We Solved Them

Real engineering is the debugging. Every one of these was hit and fixed:

| Hurdle | Root cause | Fix |
|--------|-----------|-----|
| `langchain.evaluation` ModuleNotFound | LangChain 1.x removed the module | Rewrote evaluators with LCEL (`prompt \| model \| parser`) |
| Eval schema tried to bind `{score}` | Single braces read as template vars | Escaped to `{{...}}` in the schema string |
| Chunk-size assertion failed | Tail chunk shorter than full size | Assert `len(tail) <= size`, not `==` |
| Railway build FAILED (Nixpacks) | No Dockerfile/Procfile | Added Procfile, railway.toml, then full Dockerfile |
| Image ballooned to 3.1 GB | `headroom-ai[all]` pulled PyTorch/HF; build tools left in image | Multi-stage Dockerfile; dropped the heavy dep |
| `git add` blocked by .gitignore | `telegram/` and `memory/` rules too broad | Scoped to `/telegram/`, `/memory/` (root-only) |
| Telegram dropped voice silently | Telegram needs OGG Opus, not MP3 | ffmpeg subprocess MP3→OGG; ffmpeg in runtime image |
| ElevenLabs 402 payment_required | Default "Rachel" voice needs a paid plan | Switched default to free-tier "Adam" |
| Shelby asked for API keys | Prompt didn't know its own environment | Added environment-awareness to the system prompt |
| Shelby felt "dumb" / passive | System prompt too soft | Aggressive rewrite: ban "I don't know", mandate tools |
| `$PORT` literal in start command | Railway doesn't shell-expand startCommand | Wrapped in `sh -c '... ${PORT:-8000}'` |
| `data/` gitignored the skills too | Blanket `data/` ignore | Added `!data/skills/` exceptions |
| Builder ran Railpack, no ffmpeg | railway.toml/nixpacks.toml conflicted; Railway defaulted | Pinned `builder = "DOCKERFILE"`; deleted nixpacks.toml |
| Deploy stuck "Queued" | **GitHub platform incident** (upstream) | Out of our control — code armed; auto-deploys on GitHub recovery |
| `import kaggle` risked crashing Shelby | Package calls `sys.exit(1)` at import time with no credentials configured (confirmed by testing) | Never import the package directly — shell out to the `kaggle` CLI via subprocess, which fails safely |
| **Fabricated results on real files** (dealbreaker) | inspect/fix/evaluate skills were hardwired to the synthetic customers×orders schema and silently ran the demo, narrating fake "orders/$amount" findings for a real Airbnb file | Built `dataquality/generic.py` — a real single-table loop; skills now take `path=` and analyse the actual file, labelling synthetic output "⚠️ NOT a real file"; system prompt enforces passing the real path |

_Note: the co-author commit convention was ruled out as a cause of the queue —
trailers are parsed after push and never touch builds._

---

## 4. Tech Stack (and when each piece joined)

| Layer | Technology | Added in phase |
|-------|-----------|----------------|
| LLM | Claude (Anthropic SDK) — sonnet-5 primary | Foundation |
| Agent | Custom tool-use loop, function calling | Foundation |
| RAG | ChromaDB (persistent, cosine HNSW) | Foundation |
| API | FastAPI + uvicorn, SSE streaming | Foundation |
| Validation | Pydantic v2 | Foundation |
| Evals | LangChain 1.x LCEL (LLM-as-judge) | Proof |
| Deploy | Railway, multi-stage Docker | Reach |
| Chat | python-telegram-bot v21 | Reach |
| Voice out | ElevenLabs TTS + ffmpeg (MP3→OGG) | Reach |
| Voice in | ElevenLabs Scribe STT | Reach |
| Search | Tavily API | Autonomy |
| Memory | JSON key-value NotesStore | Autonomy |
| Skills | Dynamic Python skill registry (importlib) | Autonomy |
| Reliability | Model fallback chain | Reliability |
| Observability | Custom token/cost usage tracker | Reliability |
| Scheduling | APScheduler (heartbeat + cron) | Reliability |
| Data quality | pandas — inspect/fix/evaluate loop | Flagship |
| Web UI | Vanilla HTML/CSS/JS, streaming, markdown | Showcase |
| Web UI | Glassmorphism 2.0, animated SVG emblem, live scorecard | Showcase |
| Web UI | Terminal theme, Motion (Framer engine), file+voice input | Showcase |
| Type | Share Tech Mono + Orbitron (90s-terminal scientific) | Showcase |
| Voice/File | Browser MediaRecorder → /stt; CSV upload → /inspect/upload | Showcase |
| Real-world data | Kaggle CLI (subprocess-wrapped) — kaggle_search / kaggle_download | Real-world data |
| Design | 2026 AI-UI research (glass, neon glow, shimmer=processing) | Showcase |

---

## 5. Architecture

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

## 6. What's built (feature audit)

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
| 16 | **Real-world data** — Kaggle search/download + generic single-CSV loop | `shelby/integrations/`, `dataquality/generic.py` | ✅ |
| 17 | **Self-improvement** — self-critique, learns durable lessons | `shelby/selfcritique.py` | ✅ |
| 18 | **Persistent state** — all state under SHELBY_DATA_DIR (volume-ready) | `shelby/paths.py` | ✅ |
| 19 | **External services (MCP)** — connect hosted MCP servers (Apollo, Gmail, Calendar…) via Anthropic's remote connector; add any by URL at runtime (connect_mcp), like Claude's connectors | `shelby/mcp/` | ✅ |

---

## 7. Flagship capability: the Data-Quality FDE Loop

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

## 8. Deployment

- **Platform:** Railway (project `endearing-solace` / `production`)
- **URL:** https://shelby-ai-production.up.railway.app
- **Builder:** Dockerfile (pinned) — multi-stage, slim runtime with ffmpeg
- **Start command:** `uvicorn shelby.api.main:app --host 0.0.0.0 --port $PORT`
- **Both surfaces in one process:** FastAPI serves the web UI; the Telegram bot
  starts as a background task inside the FastAPI lifespan.

### Environment variables (set in Railway, never committed)
| Var | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | Claude API |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `ELEVENLABS_API_KEY` | TTS + STT |
| `TAVILY_API_KEY` | Web search |
| `KAGGLE_API_TOKEN` | Kaggle dataset search/download (optional) |
| `SHELBY_DATA_DIR` | Base dir for all persistent state — set to the volume mount (e.g. `/data`) |
| `SHELBY_MCP_SERVERS` / `SHELBY_MCP_<NAME>_URL` + `_TOKEN` | Connect hosted MCP servers (Apollo, Gmail, Calendar…). See `docs/MCP_SETUP.md`. All optional — connector is inactive if unset |

### Persistence (Railway volume)
All writable state (memory, learned lessons, usage, scheduled tasks, RAG,
learned skills) lives under `SHELBY_DATA_DIR`. To make it survive redeploys:
1. Railway → service → **Volumes** → add a volume, mount path `/data`.
2. Railway → **Variables** → set `SHELBY_DATA_DIR=/data`.
Bundled skills are auto-seeded onto the empty volume on first boot, so nothing
is lost. Without a volume, state persists only for the life of the container.

---

## 9. Repo layout

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
├── mcp/                remote MCP connector (hosted external services)
├── telegram/           bot (text/voice)
├── tts/ · stt/         ElevenLabs voice
└── evals/              LangChain LLM-as-judge
data/skills/            bundled skills (usage stats, data-quality loop)
tests/                  tool, RAG, eval-structure tests
```

---

## 10. Known follow-ups

- Confirm the deployed image is built from the **Dockerfile** (now pinned) so
  ffmpeg is present and voice works.
- Rotate any API keys/tokens that were ever shared in plaintext.
- Optional: expose the data-quality scorecard as a visual panel in the web UI.
