# Shelby: Project Status and Engineering Log

A living record of what Shelby is, how it was built, and the problems solved along the way.

Last updated: 2026-07-23.

Shelby is a production AI agent built on the Claude API to demonstrate Forward-Deployed Engineering work: taking a real, messy problem and shipping an agent that solves it end to end. It runs as one deployed service that exposes a web chat interface and a Telegram bot, backed by tool use, retrieval-augmented memory, a data-quality repair loop, scheduling, voice, MCP connectors, and incoming webhooks. The package is about 4,600 lines of Python.

## The story

Shelby did not begin as an AI project. It started as an empty configuration repository: a pile of runtime files and scaffolding with no product inside. The goal was fixed early and never changed, to build a real agent with tool use, RAG, a FastAPI backend, and LangChain evaluations, ready to pitch for an FDE role.

It grew in deliberate phases, and each phase had to earn its place by making Shelby more useful on a real task rather than adding features for their own sake.

1. Foundation. Strip the old runtime and build a working Python agent with Claude tool use, a ChromaDB knowledge base, and a FastAPI server.
2. Proof. Add a LangChain LLM-as-judge evaluation suite and iterate until the scores held.
3. Reach. Put Shelby on Telegram, then add voice input and output with ElevenLabs, matching the reply format to the input (text for text, voice for voice).
4. Autonomy. Add live web search, self-writing memory, and a skill system Shelby can extend at runtime.
5. Reliability. Add a model fallback chain, token and cost tracking, and a scheduler for recurring work.
6. The real problem. After rejecting a throwaway machine-learning demo, settle on the authentic FDE task: inspect, repair, and score broken enterprise data, proven with a before-and-after quality score.
7. Showcase. Ship a public web interface so anyone can try Shelby live, and keep this document as the single source of truth.

## Milestones

| Area | Milestone | Outcome |
|------|-----------|---------|
| Foundation | Empty repo becomes a real Python agent | Old runtime removed, agent loop running |
| Foundation | Tool use, RAG, and FastAPI shipped | Core stack working end to end |
| Proof | LangChain evaluations pass | LLM-as-judge harness green |
| Reach | Deployed to Railway | Public service running |
| Reach | Telegram bot connected | Usable from a phone |
| Reach | Voice input and output | Replies match the input format |
| Autonomy | Web search through Tavily | Answers use live data |
| Autonomy | Self-writing memory and dynamic skills | Shelby learns and remembers |
| Reliability | Model fallback chain | Survives rate limits and outages |
| Reliability | Token and cost tracking | Every call accounted for |
| Reliability | Heartbeat and cron scheduling | Runs on its own schedule |
| Flagship | Data-quality repair loop | Quality score 76 to 100 on broken data |
| Showcase | Public web chat interface | Anyone can try it live |
| Showcase | In-app data-quality scorecard | One click renders a before-and-after result |
| Real-world data | Kaggle search and download | Shelby finds and profiles real datasets itself |
| Self-improvement | Self-critique skill | Learns durable lessons and applies them later |
| File delivery | File sending to Telegram and web | Delivers generated files safely |
| External services | MCP connectors | Connects hosted services, by config or by URL at runtime |
| Evaluation | LangSmith experiment | Faithfulness 1.00, relevance 1.00, conciseness 0.91 |
| Automation | Incoming webhooks | External events (a file landing, a GitHub push) trigger a saved skill automatically |
| Interface | Minimalist redesign | Clean light and dark themes, mobile responsive |
| Automation | Schema drift detection | A recurring export is checked against a remembered baseline; column changes are flagged before they break anything |

## Engineering problems solved

The real work was in the debugging. Each of these was hit and fixed.

| Problem | Cause | Fix |
|---------|-------|-----|
| `langchain.evaluation` not found | LangChain 1.x removed the module | Rewrote the evaluators using LCEL (`prompt \| model \| parser`) |
| Eval schema read `{score}` as a variable | Single braces are template placeholders | Escaped them to `{{...}}` in the schema string |
| Chunk-size assertion failed | The final chunk is shorter than a full chunk | Assert the tail is at most the chunk size, not exactly equal |
| Railway build failed | No Dockerfile or Procfile present | Added a multi-stage Dockerfile |
| Image grew to 3.1 GB | A heavy dependency pulled in PyTorch | Removed it and used a slim multi-stage build |
| Telegram dropped voice silently | Telegram needs OGG Opus, not MP3 | Convert with ffmpeg, and include ffmpeg in the runtime image |
| ElevenLabs returned payment required | The default voice needs a paid plan | Switched to a free-tier voice |
| Shelby kept asking for API keys | The prompt did not know its own environment | Told the prompt which keys are already configured |
| The start command broke on Railway | Railway does not expand `$PORT` in the raw command | Wrapped it in a shell so the variable expands |
| Bundled skills were ignored by git | A broad `data/` ignore rule caught them | Added explicit exceptions for the skills directory |
| Importing the Kaggle package crashed | It calls `sys.exit(1)` at import with no credentials | Never import it; call the Kaggle CLI through a subprocess |
| Fabricated results on real files | The data skills silently ran a synthetic demo and narrated fake findings for a real file | Built a real single-table loop, made every skill take the actual file path, and added a rule that forbids passing synthetic output off as real |
| Evaluations: answers ignored the context | The aggressive prompt made Shelby answer from its own knowledge instead of the provided context | Added a context-grounding rule so a provided context block becomes the single source of truth |
| Evaluations: relevance judge was context-blind | The judge only saw the question and answer, so it second-guessed correct answers | Made the relevance judge context-aware, scoring completeness only against what the context supports |

## Tech stack

| Layer | Technology |
|-------|------------|
| Model | Claude (Anthropic SDK), with a fallback chain |
| Agent | Custom tool-use loop with function calling |
| Retrieval | ChromaDB, persistent, cosine similarity |
| API | FastAPI and uvicorn, with server-sent event streaming |
| Validation | Pydantic v2 |
| Evaluation | LangChain LCEL judges and LangSmith experiments |
| Deployment | Railway, multi-stage Docker |
| Chat | python-telegram-bot |
| Voice | ElevenLabs for speech to text and text to speech, ffmpeg for conversion |
| Search | Tavily |
| Memory | JSON key-value store for structured facts |
| Skills | Dynamic Python skill registry |
| Scheduling | APScheduler for the heartbeat and cron jobs |
| Data quality | pandas, in the inspect-repair-score loop |
| External services | Remote MCP connectors |
| Web UI | Vanilla HTML, CSS, and JavaScript, light and dark themes |

## Architecture

```
FastAPI service (uvicorn)
  Web chat UI        GET /
  REST API           /chat, /chat/stream, /rag/*, /mcp, /health
  Telegram bot       runs in the same event loop
  Task scheduler     heartbeat and cron jobs

  ShelbyAgent (core loop)
    Model fallback chain
    Tool-use loop (Claude function calling)
    Token and cost tracking
    25 tools: web search, knowledge base, memory, skills,
              scheduling, Kaggle, file delivery, MCP connectors, webhooks, drift detection,
              and the data-quality skills
```

## Features

| # | Capability | Location |
|---|-----------|----------|
| 1 | Tool use, 25 tools through Claude function calling | `shelby/tools.py` |
| 2 | Retrieval memory with ChromaDB | `shelby/rag/` |
| 3 | FastAPI server, REST and streaming | `shelby/api/main.py` |
| 4 | LangChain and LangSmith evaluations | `shelby/evals/` |
| 5 | Web chat interface | `shelby/api/static/index.html` |
| 6 | Telegram bot, text and voice | `shelby/telegram/bot.py` |
| 7 | Voice output with ElevenLabs | `shelby/tts/elevenlabs.py` |
| 8 | Voice input with ElevenLabs | `shelby/stt/elevenlabs.py` |
| 9 | Persistent structured memory | `shelby/memory/notes.py` |
| 10 | Dynamic skills at runtime | `shelby/skills/registry.py` |
| 11 | Live web search with Tavily | `shelby/tools.py` |
| 12 | Model fallback on transient errors | `shelby/agent.py` |
| 13 | Token and cost tracking | `shelby/usage_tracker.py` |
| 14 | Heartbeat and cron scheduling | `shelby/scheduler.py` |
| 15 | Data-quality repair loop | `shelby/dataquality/` |
| 16 | Kaggle search and download | `shelby/integrations/` |
| 17 | Self-critique that learns from mistakes | `shelby/selfcritique.py` |
| 18 | Persistent state for volumes | `shelby/paths.py` |
| 19 | MCP connectors, by config or by URL | `shelby/mcp/` |
| 20 | Incoming webhooks, external events trigger a saved skill | `shelby/webhooks/` |
| 21 | Schema drift detection for recurring datasets | `shelby/dataquality/drift.py` |

## Evaluation results

Shelby's answers are scored by a LangChain LCEL judge on faithfulness (no claims beyond the context), relevance (answers the question given the context), and conciseness. The suite runs against real Claude calls and is traced as a LangSmith experiment (`shelby-qa-a12b89b9`).

| Question | Faithfulness | Relevance | Conciseness |
|----------|:------------:|:---------:|:-----------:|
| What is Shelby? | 1.00 | 1.00 | 0.85 |
| How does Shelby remember things across sessions? | 1.00 | 1.00 | 0.90 |
| What tools can Shelby use? | 1.00 | 1.00 | 1.00 |
| What API does Shelby expose? | 1.00 | 1.00 | 0.90 |

Every example passes the gate (faithfulness and relevance at 1.00, conciseness averaging 0.91), and the results hold across repeated runs. The 27 structural tests stay green. The raw export and a summary are committed under `evals_results/` and `docs/EVAL_RESULTS.md`.

Run it locally:

```bash
export ANTHROPIC_API_KEY=...
python -m shelby.evals.run
```

Run it as a LangSmith experiment (needs network access to `api.smith.langchain.com`):

```bash
export LANGSMITH_TRACING=true LANGSMITH_API_KEY=... LANGSMITH_PROJECT=Shelby
export ANTHROPIC_API_KEY=...
python -m shelby.evals.langsmith_run
```

## The data-quality repair loop

This is the strongest demonstration, and the exact job a Palantir FDE does. It takes two broken source exports, a CRM customers file and an ERP orders file that have to join, and runs the full loop.

1. Inspect. Diagnose seven classes of defect: duplicate rows, nulls, inconsistent date formats, categorical casing and typos, broken referential integrity, messy numeric formatting, and whitespace or casing noise on the join keys.
2. Repair. Apply the transformations, quarantine unrecoverable orphaned rows for review rather than dropping them, and return a full changelog.
3. Score. Produce an objective before-and-after quality score across completeness, uniqueness, validity, consistency, integrity, and key hygiene.

The result is a quality score of 76 improving to 100, with 29 unrecoverable rows quarantined. The same three steps are exposed as agent skills, so Shelby can run the whole loop in conversation. Shelby also profiles and repairs arbitrary real single CSV files.

```bash
python -m shelby.dataquality.demo
```

### Schema drift detection

A one-off cleanup is useful, but the real FDE problem is a dataset that arrives over and over, and quietly changes shape underneath you. `shelby/dataquality/drift.py` fingerprints a CSV's columns and a coarse inferred type per column (numeric, date, or text), and remembers that fingerprint under a name. The first check saves the baseline; every check after that diffs the current file against it and reports exactly what changed: columns added, columns removed, or a column's type flipping.

This is what makes the webhook feature into an actual pipeline rather than a one-shot trigger: bind `check_schema_drift` to a webhook on a recurring export, and Shelby flags a broken upstream schema change the moment the next file lands, before anyone downstream notices bad data. `reset_schema_baseline` approves an intentional change so it stops being reported.

## Deployment

Shelby runs on Railway from a pinned multi-stage Dockerfile that includes ffmpeg for voice. The FastAPI service serves the web interface and starts the Telegram bot as a background task in the same process.

All writable state (memory, learned lessons, usage, scheduled tasks, the vector store, and learned skills) lives under `SHELBY_DATA_DIR`. Mounting a Railway volume there keeps it across redeploys, and bundled skills are copied onto an empty volume on first boot. Without a volume, state lasts for the life of the container.

Secrets are set as Railway environment variables and never committed:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `ELEVENLABS_API_KEY` | Voice input and output |
| `TAVILY_API_KEY` | Web search |
| `KAGGLE_API_TOKEN` | Kaggle search and download (optional) |
| `SHELBY_DATA_DIR` | Base directory for persistent state, set to the volume mount |
| `LANGSMITH_*` | LangSmith tracing and experiments (optional) |
| `SHELBY_MCP_*` | Connect hosted MCP servers (optional). See `docs/MCP_SETUP.md` |
| `TELEGRAM_NOTIFY_CHAT_ID` | Chat to notify when an incoming webhook fires (optional). See `docs/WEBHOOKS.md` |

## Repository layout

```
shelby/
  agent.py         core agent loop, model fallback, system prompt
  tools.py         25 tools and the dispatcher
  scheduler.py     heartbeat and cron jobs
  usage_tracker.py token and cost accounting
  api/             FastAPI: main.py, models.py, static/index.html
  dataquality/     inspect, repair, and score loop
  rag/             ChromaDB store and ingestion
  memory/          structured key-value store
  skills/          dynamic skill registry
  mcp/             remote MCP connectors
  webhooks/        incoming webhook registry
  telegram/        the Telegram bot
  tts/ stt/        ElevenLabs voice
  evals/           LangChain and LangSmith evaluations
data/skills/       bundled skills
evals_results/     saved evaluation runs
tests/             tool, RAG, and eval tests
```

## Follow-ups

- Add the LangSmith evaluation screenshot to the README.
- Rotate any API keys or tokens that were shared in plaintext during development.
