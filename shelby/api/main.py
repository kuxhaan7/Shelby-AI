"""Shelby FastAPI server — web chat UI, REST API, and embedded Telegram bot."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ..agent import ShelbyAgent
from ..memory.notes import NotesStore
from ..rag.ingest import ingest_text, ingest_workspace
from ..rag.store import RagStore
from ..skills.registry import SkillRegistry
from .models import (
    ChatRequest,
    ChatResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ── App state ────────────────────────────────────────────────────────────────

rag_store: RagStore | None = None
agent: ShelbyAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_store, agent

    rag_store = RagStore()
    agent = ShelbyAgent(
        rag_store=rag_store,
        notes_store=NotesStore(),
        skill_registry=SkillRegistry(),
    )

    workspace = os.getenv("SHELBY_WORKSPACE", "./workspace")
    if os.path.isdir(workspace):
        await run_in_threadpool(ingest_workspace, rag_store, workspace)

    # Start Telegram bot in the same event loop when token is available
    tg_app = None
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        try:
            from ..telegram.bot import build_app as build_tg_app
            tg_app = build_tg_app(agent)
            await tg_app.initialize()
            await tg_app.start()
            await tg_app.updater.start_polling(drop_pending_updates=True)
            log.info("Telegram bot started alongside FastAPI")
        except Exception:
            log.exception("Failed to start Telegram bot — continuing without it")
            tg_app = None

    yield

    if tg_app is not None:
        try:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        except Exception:
            log.exception("Error shutting down Telegram bot")


app = FastAPI(
    title="Shelby AI",
    description="Claude-powered agent with persistent RAG memory and tool use.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Web UI ───────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


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
    # Run synchronous agent in a thread so the event loop stays free
    reply, usage = await run_in_threadpool(agent.chat_with_usage, messages)
    return ChatResponse(reply=reply, model=usage.model)


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
    n = await run_in_threadpool(ingest_text, rag_store, req.text, req.source)
    return IngestResponse(chunks_added=n, total_docs=rag_store.count())


@app.post("/rag/search", response_model=SearchResponse)
async def rag_search(req: SearchRequest):
    if rag_store is None:
        raise HTTPException(503, "RAG store not ready")
    raw = await run_in_threadpool(rag_store.query, req.query, req.n_results)
    return SearchResponse(results=[SearchResult(**r) for r in raw])
