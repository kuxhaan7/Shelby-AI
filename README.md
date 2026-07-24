# Shelby

Shelby is a production AI agent built on the Claude API. It does the core work of a Forward-Deployed Engineer: take a real, messy problem, usually broken enterprise data, and fix it from start to finish. It runs as a single deployed service that exposes a web chat interface and a Telegram bot, backed by tool use, retrieval-augmented memory, a data-quality repair loop, and a LangChain evaluation suite, deployed on Railway.

Live demo: https://shelby-ai-production.up.railway.app
Author: [Kushaan Kaushik](https://github.com/kuxhaan7) ([LinkedIn](https://linkedin.com/in/kushaankaushik))

## What it does

The headline capability is a data-quality repair loop, the same job a Palantir FDE does on a customer's data. Give Shelby a broken dataset and it runs three steps:

1. Inspect. Diagnose the defects: nulls, duplicate rows, broken joins, inconsistent date formats, categorical casing and typos, and messy numeric formatting.
2. Repair. Apply the fixes and quarantine unrecoverable rows for review instead of dropping them silently. Every transformation is logged in a changelog.
3. Score. Produce an objective before-and-after quality score across completeness, uniqueness, validity, consistency, integrity, and key hygiene.

On a deliberately broken two-table dataset (a CRM export and an ERP export that have to join), the quality score improved from 76 to 100, with 29 unrecoverable rows quarantined rather than dropped. Shelby also profiles and repairs arbitrary real single CSV files, and can pull public datasets from Kaggle to test against.

Beyond the data loop, Shelby holds a conversation, searches the live web, remembers facts across sessions, writes and runs its own Python skills, schedules recurring jobs, speaks and listens, and connects to external services through MCP.

## Evaluation results

Quality is measured, not assumed. A LangChain LLM-as-judge suite scores Shelby's answers on faithfulness (no claims beyond the given context), relevance, and conciseness. Runs are traced and scored as a LangSmith experiment.

| Question | Faithfulness | Relevance | Conciseness |
|----------|:------------:|:---------:|:-----------:|
| What is Shelby? | 1.00 | 1.00 | 0.85 |
| How does Shelby remember things across sessions? | 1.00 | 1.00 | 0.90 |
| What tools can Shelby use? | 1.00 | 1.00 | 1.00 |
| What API does Shelby expose? | 1.00 | 1.00 | 0.90 |

Every example passes the gate (faithfulness and relevance at 1.00, conciseness averaging 0.91), and the results stay stable across repeated runs. The raw LangSmith export and a summary live in `evals_results/` and `docs/EVAL_RESULTS.md`.

## Features

| Capability | Detail |
|-----------|--------|
| Tool use | 19 tools driven by Claude function calling |
| Data-quality loop | Inspect, repair, and score broken datasets |
| Retrieval memory | ChromaDB vector store with chunked ingestion |
| Persistent memory | Structured facts about the user, kept across sessions |
| Web search | Live results through Tavily |
| Dynamic skills | Shelby writes and runs new Python skills at runtime |
| Scheduling | A heartbeat loop plus cron jobs through APScheduler |
| Voice | Speech to text and text to speech with ElevenLabs |
| MCP connectors | Connect Gmail, Notion, Apollo, and others by URL, at runtime or through config |
| Self-critique | Learns durable lessons from its own mistakes |
| Model fallback | Falls back across models on rate limits and outages |
| Interfaces | Web chat UI and a Telegram bot in one process |

## Quick start

```bash
# 1. Install dependencies
./scripts/setup.sh

# 2. Set your Claude API key
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 3. Run the server
./scripts/serve.sh
# then open http://localhost:8000

# 4. Run the LangChain evaluations
python -m shelby.evals.run
```

To watch the data-quality loop take a broken dataset from 76 to 100:

```bash
python -m shelby.dataquality.demo
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server health and connected MCP servers |
| POST | `/chat` | Chat with tool use, returns any files Shelby produced |
| POST | `/chat/stream` | Streaming chat over server-sent events |
| POST | `/inspect/upload` | Profile an uploaded CSV for data-quality issues |
| GET | `/demo/dataquality` | Run the inspect-repair-score loop, return a scorecard |
| POST | `/rag/ingest` | Add documents to the knowledge base |
| POST | `/rag/search` | Semantic search over the knowledge base |
| GET/POST/DELETE | `/mcp` | List, connect, or remove MCP services by URL |

Example chat request:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What time is it, and what is 17 * 23?"}]}'
```

## Deployment

Shelby runs on Railway from a multi-stage Dockerfile that includes ffmpeg for voice. The FastAPI service serves the web UI and starts the Telegram bot as a background task in the same process. All writable state (memory, learned skills, usage, scheduled tasks, the vector store) lives under `SHELBY_DATA_DIR`, so mounting a Railway volume there keeps it across redeploys.

Secrets are set as Railway environment variables and never committed: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `TAVILY_API_KEY`, and optionally `KAGGLE_API_TOKEN` and the `LANGSMITH_*` and `SHELBY_MCP_*` variables. See `docs/MCP_SETUP.md` for connecting external services.

## Tech

Python, Claude API (tool use), FastAPI, ChromaDB, LangChain and LangSmith, Tavily, ElevenLabs, python-telegram-bot, APScheduler, pandas, Docker, Railway.

## Screenshots

<img width="1183" height="783" alt="Shelby web interface" src="https://github.com/user-attachments/assets/b653ea86-974a-4eac-8a80-dbd7f1464e6f" />
<img width="1184" height="785" alt="Shelby data-quality scorecard" src="https://github.com/user-attachments/assets/16d663f6-1334-4685-8e42-8e5c16cf7b56" />

---

Built by [Kushaan Kaushik](https://github.com/kuxhaan7). [Portfolio](https://kuxhaan7.github.io) · [LinkedIn](https://linkedin.com/in/kushaankaushik)
