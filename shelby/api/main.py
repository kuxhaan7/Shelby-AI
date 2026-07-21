"""Shelby FastAPI server — chat, RAG ingest, and semantic search endpoints."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ..agent import ShelbyAgent
from ..rag.ingest import ingest_text, ingest_workspace
from ..rag.store import RagStore
from .models import (
    ChatRequest,
    ChatResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

# ── App state ────────────────────────────────────────────────────────────────

rag_store: RagStore | None = None
agent: ShelbyAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_store, agent
    rag_store = RagStore()
    agent = ShelbyAgent(rag_store=rag_store)

    # Pre-load workspace docs on startup
    workspace = os.getenv("SHELBY_WORKSPACE", "./workspace")
    if os.path.isdir(workspace):
        ingest_workspace(rag_store, workspace)

    yield


app = FastAPI(
    title="Shelby AI",
    description="Claude-powered agent with persistent RAG memory and tool use.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    docs = rag_store.count() if rag_store else 0
    return {"status": "ok", "rag_docs": docs}


# ── Chat ─────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if agent is None:
        raise HTTPException(503, "Agent not ready")
    if req.stream:
        raise HTTPException(400, "Use /chat/stream for streaming responses")

    messages = [m.model_dump() for m in req.messages]
    reply = agent.chat(messages)
    return ChatResponse(reply=reply, model=os.getenv("SHELBY_MODEL", "claude-sonnet-5"))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if agent is None:
        raise HTTPException(503, "Agent not ready")
    messages = [m.model_dump() for m in req.messages]

    async def _gen() -> AsyncGenerator[str, None]:
        for chunk in agent.stream(messages):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── RAG ──────────────────────────────────────────────────────────────────────

@app.post("/rag/ingest", response_model=IngestResponse)
async def rag_ingest(req: IngestRequest):
    if rag_store is None:
        raise HTTPException(503, "RAG store not ready")
    n = ingest_text(rag_store, req.text, source=req.source)
    return IngestResponse(chunks_added=n, total_docs=rag_store.count())


@app.post("/rag/search", response_model=SearchResponse)
async def rag_search(req: SearchRequest):
    if rag_store is None:
        raise HTTPException(503, "RAG store not ready")
    raw = rag_store.query(req.query, n_results=req.n_results)
    return SearchResponse(results=[SearchResult(**r) for r in raw])
