"""Shelby FastAPI server — web chat UI, REST API, and embedded Telegram bot."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ..agent import ShelbyAgent
from ..memory.notes import NotesStore
from ..rag.ingest import ingest_text, ingest_workspace
from ..rag.store import RagStore
from ..scheduler import TaskScheduler
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
    skills = SkillRegistry()
    scheduler = TaskScheduler(skill_registry=skills)
    agent = ShelbyAgent(
        rag_store=rag_store,
        notes_store=NotesStore(),
        skill_registry=skills,
        task_scheduler=scheduler,
    )

    workspace = os.getenv("SHELBY_WORKSPACE", "./workspace")
    if os.path.isdir(workspace):
        await run_in_threadpool(ingest_workspace, rag_store, workspace)

    await run_in_threadpool(scheduler.start)

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

    scheduler.shutdown()

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


# ── Data-quality demo (flagship FDE loop) ────────────────────────────────────

@app.get("/demo/dataquality")
async def demo_dataquality():
    """Run the inspect→fix→evaluate loop and return before/after scorecard JSON."""

    def _run():
        from ..dataquality.cleaner import clean_dataset
        from ..dataquality.evaluate import evaluate_quality
        from ..dataquality.generate_broken import generate
        from ..dataquality.profiler import profile_dataset

        gen = generate()
        cust, orders = gen["customers"], gen["orders"]
        report = profile_dataset(cust, orders)
        before = evaluate_quality(cust, orders)
        result = clean_dataset(cust, orders)
        after = evaluate_quality(result["customers_clean"], result["orders_clean"])

        defects = list(report.get("defects", []))
        for tbl in report.get("tables", {}).values():
            defects.extend(tbl.get("issues", []))

        return {
            "before": before,
            "after": after,
            "changelog": result["changelog"],
            "defects": defects,
            "quarantined": result["quarantined_rows"],
        }

    return await run_in_threadpool(_run)


# ── Voice input (STT) ────────────────────────────────────────────────────────

@app.post("/stt")
async def stt(audio: UploadFile = File(...)):
    """Transcribe an uploaded audio clip (browser mic recording) to text."""
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise HTTPException(503, "Voice input unavailable (ELEVENLABS_API_KEY not set)")
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty audio upload")

    from ..stt.elevenlabs import transcribe
    mime = audio.content_type or "audio/webm"
    text = await run_in_threadpool(transcribe, data, mime)
    if not text:
        raise HTTPException(422, "Could not transcribe audio")
    return {"text": text}


# ── File input (upload a CSV → generic data-quality profile) ─────────────────

@app.post("/inspect/upload")
async def inspect_upload(file: UploadFile = File(...)):
    """Profile any uploaded CSV: rows, columns, duplicates, nulls, quality score."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")

    def _profile(raw_bytes: bytes, name: str) -> dict:
        import gzip
        import io
        import re

        import pandas as pd

        # Transparently handle gzip — Inside Airbnb and many real government
        # exports ship as .csv.gz, and browsers won't auto-decompress on upload.
        if raw_bytes[:2] == b"\x1f\x8b":
            try:
                raw_bytes = gzip.decompress(raw_bytes)
            except Exception as exc:
                return {"error": f"Could not decompress .gz file: {exc}"}

        MAX_ROWS = 300_000  # keep large real-world exports (e.g. reviews.csv) fast + memory-safe
        try:
            df_iter = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=True,
                                   chunksize=MAX_ROWS)
            df = next(df_iter)
            truncated = False
            try:
                next(df_iter)
                truncated = True
            except StopIteration:
                pass
        except Exception as exc:
            return {"error": f"Could not parse CSV: {exc}"}

        n = len(df)
        cols = list(df.columns)
        dupes = int(df.duplicated().sum())
        nulls = {c: int(df[c].isna().sum() + (df[c] == "").sum()) for c in cols}
        total_cells = n * len(cols) if cols else 0
        total_nulls = sum(nulls.values())

        # Format-level defects: currency strings, percent strings — the exact
        # issues real exports like Inside Airbnb's price/host_response_rate ship with.
        currency_re = re.compile(r"^\s*\$[\d,]+\.?\d*\s*$")
        percent_re = re.compile(r"^\s*\d+(\.\d+)?%\s*$")
        format_issues = []
        for col in cols:
            sample = df[col].dropna().astype(str).head(500)
            if sample.empty:
                continue
            if (sample.str.match(currency_re)).mean() > 0.5:
                format_issues.append(f"'{col}' stores currency as text (e.g. \"{sample.iloc[0]}\") — needs numeric coercion")
            elif (sample.str.match(percent_re)).mean() > 0.5:
                format_issues.append(f"'{col}' stores percentages as text (e.g. \"{sample.iloc[0]}\") — needs numeric coercion")

        completeness = round(100 * (1 - total_nulls / total_cells), 1) if total_cells else 100.0
        uniqueness = round(100 * (1 - dupes / n), 1) if n else 100.0
        overall = round((completeness + uniqueness) / 2, 1)

        issues = list(format_issues)
        if dupes:
            issues.append(f"{dupes} duplicate rows")
        for col, cnt in sorted(nulls.items(), key=lambda x: -x[1])[:5]:
            if cnt:
                issues.append(f"{cnt} missing values in '{col}'")

        return {
            "filename": name,
            "rows": n,
            "truncated": truncated,
            "columns": cols,
            "duplicate_rows": dupes,
            "missing_cells": total_nulls,
            "scores": {"completeness": completeness, "uniqueness": uniqueness, "overall": overall},
            "issues": issues[:8],
        }

    return await run_in_threadpool(_profile, raw, file.filename or "upload.csv")


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
