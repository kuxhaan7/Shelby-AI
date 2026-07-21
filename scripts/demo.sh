#!/usr/bin/env bash
# Quick smoke-test against a running Shelby API server.
set -euo pipefail

BASE=${SHELBY_API_URL:-http://localhost:8000}

echo "==> Shelby API Demo"
echo "    Target: $BASE"
echo ""

# Health check
echo "--- /health ---"
curl -sf "$BASE/health" | python3 -m json.tool
echo ""

# Ingest a document
echo "--- POST /rag/ingest ---"
curl -sf -X POST "$BASE/rag/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Shelby is a Claude-powered autonomous agent with persistent memory, tool use, RAG, and a FastAPI backend. It integrates with Telegram and WhatsApp.",
    "source": "demo"
  }' | python3 -m json.tool
echo ""

# Semantic search
echo "--- POST /rag/search ---"
curl -sf -X POST "$BASE/rag/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is Shelby?", "n_results": 2}' | python3 -m json.tool
echo ""

# Chat with tool use
echo "--- POST /chat (tool use) ---"
curl -sf -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What time is it and what is 17 * 23?"}
    ]
  }' | python3 -m json.tool
echo ""

echo "==> Demo complete"
