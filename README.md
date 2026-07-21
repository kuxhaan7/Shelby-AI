
# Shelby-AI

An autonomous agent built on the Claude API with persistent RAG memory, tool use, a FastAPI backend, and LangChain evaluations.

**Status:** Active personal project. Integrates personal accounts and runs on personal infrastructure — reach out via [LinkedIn](https://linkedin.com/in/kushaankaushik) for a walkthrough or live demo.

## What Shelby Does

Shelby is a long-running agent that:

- **Stays alive.** A heartbeat loop keeps Shelby responsive between conversations, carrying context across days.
- **Talks naturally.** Conversational access through Telegram and WhatsApp — no terminal, no UI to learn.
- **Acts, not just answers.** Claude tool use lets Shelby do real work: calculations, time lookups, knowledge-base search, and memory writes.
- **Carries memory.** ChromaDB-backed RAG separates short-term conversation context from long-term semantic storage, so Shelby gets smarter over time.
- **Measures quality.** A LangChain eval suite scores responses on faithfulness and relevance against a ground-truth dataset.

## Architecture

```
shelby/
├── agent.py          # Agentic tool-use loop (Claude API)
├── tools.py          # Tool schemas + dispatch (calculate, search_kb, remember, …)
├── rag/
│   ├── store.py      # ChromaDB vector store
│   └── ingest.py     # Text chunking & document ingestion
├── api/
│   ├── main.py       # FastAPI server (chat, stream, RAG ingest/search, health)
│   └── models.py     # Pydantic request/response models
└── evals/
    ├── dataset.py    # Ground-truth Q&A pairs
    ├── evaluators.py # LangChain faithfulness & relevance evaluators
    └── run.py        # Eval runner with Rich table output
```

## Quick Start

```bash
# 1. Install
./scripts/setup.sh

# 2. Configure
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 3. Run the API server
./scripts/serve.sh
# → http://localhost:8000/docs

# 4. Smoke-test with the demo script
./scripts/demo.sh

# 5. Run LangChain evals
python -m shelby.evals.run
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health + RAG doc count |
| `POST` | `/chat` | Chat with Claude + tool use |
| `POST` | `/chat/stream` | Streaming SSE chat |
| `POST` | `/rag/ingest` | Add documents to ChromaDB |
| `POST` | `/rag/search` | Semantic search over knowledge base |

### Chat (with tool use)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What time is it and what is 17 * 23?"}]}'
```

### RAG Ingest

```bash
curl -X POST http://localhost:8000/rag/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Shelby uses ChromaDB for semantic memory.", "source": "readme"}'
```

### LangChain Evals

```bash
python -m shelby.evals.run --verbose
```

Outputs a Rich table with per-question faithfulness and relevance scores.

## Tech

Python · Claude API · Tool Use · FastAPI · ChromaDB · LangChain Evals · Telegram Bot API · WhatsApp Business API

<img width="1183" height="783" alt="Screenshot 2026-05-17 at 3 58 23 PM" src="https://github.com/user-attachments/assets/b653ea86-974a-4eac-8a80-dbd7f1464e6f" />
<img width="1184" height="785" alt="Screenshot 2026-05-17 at 4 00 22 PM" src="https://github.com/user-attachments/assets/16d663f6-1334-4685-8e42-8e5c16cf7b56" />

---

Built by [Kushaan Kaushik](https://github.com/kuxhaan7) · [Portfolio](https://kuxhaan7.github.io) · [LinkedIn](https://linkedin.com/in/kushaankaushik)
