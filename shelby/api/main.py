"""Shelby FastAPI server — web chat UI, REST API, and embedded Telegram bot."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import json

import anthropic
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
    FileRef,
    IngestRequest,
    IngestResponse,
    McpConnectRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
    WebhookCreateRequest,
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
    # If GOOGLE_SITE_VERIFICATION is set, inject the meta tag Google Search
    # Console needs to verify domain ownership. Kept out of the static file
    # so the value is env-configurable per deploy (Cloud Run, Railway, local).
    verification = os.getenv("GOOGLE_SITE_VERIFICATION")
    if verification:
        from fastapi.responses import HTMLResponse
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        tag = f'<meta name="google-site-verification" content="{verification}">'
        html = html.replace("</head>", f"  {tag}\n</head>", 1)
        return HTMLResponse(html)
    return FileResponse(STATIC_DIR / "index.html")


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    from ..mcp import list_servers
    from ..webhooks import registry as webhook_registry
    docs = rag_store.count() if rag_store else 0
    mcp = [{"name": s["name"], "authenticated": s["authenticated"]} for s in list_servers()]
    webhooks = len(webhook_registry.list_webhooks())
    return {"status": "ok", "rag_docs": docs, "mcp_servers": mcp, "webhooks": webhooks}


# ── MCP connectors (add any external service by URL, like Claude) ─────────────

@app.get("/mcp")
async def mcp_list():
    """List connected MCP services (tokens never returned)."""
    from ..mcp import list_servers
    return {"servers": list_servers()}


@app.post("/mcp")
async def mcp_connect(req: McpConnectRequest):
    """Connect a new MCP service by URL. Persists across restarts."""
    from ..mcp import add_server
    result = add_server(
        name=req.name, url=req.url, token=req.token, allowed_tools=req.allowed_tools,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Could not connect."))
    return {"ok": True, "name": result["name"]}


@app.delete("/mcp/{name}")
async def mcp_disconnect(name: str):
    """Disconnect a runtime-registered MCP service by name."""
    from ..mcp import remove_server
    result = remove_server(name)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Not found."))
    return {"ok": True, "name": result["name"]}


# ── Incoming webhooks (external events trigger a saved skill) ────────────────

_MAX_WEBHOOK_BYTES = 1 * 1024 * 1024  # 1MB — a webhook payload has no business being bigger


@app.get("/webhooks")
async def webhooks_list():
    """List registered webhooks (secrets never returned)."""
    from ..webhooks import registry as webhook_registry
    return {"webhooks": webhook_registry.list_webhooks()}


@app.post("/webhooks")
async def webhooks_create(req: WebhookCreateRequest):
    """Register a webhook bound to an existing skill. Returns the secret once."""
    from ..webhooks import registry as webhook_registry
    if agent is None:
        raise HTTPException(503, "Agent not ready")
    result = webhook_registry.create(
        req.name, req.skill_name, req.secret, skill_registry=agent._skills,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Could not create webhook."))
    return result


@app.delete("/webhooks/{name}")
async def webhooks_delete(name: str):
    """Remove a registered webhook by name."""
    from ..webhooks import registry as webhook_registry
    result = webhook_registry.remove(name)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Not found."))
    return result


def _run_webhook_skill(name: str, skill_name: str, payload: dict) -> None:
    """Runs in the background after a webhook is accepted; never blocks the request."""
    from ..notify import notify_telegram
    try:
        result = agent._skills.run(skill_name, payload)
    except Exception as exc:
        result = f"Webhook '{name}' -> skill '{skill_name}' raised: {exc}"
        log.error(result)
    log.info("Webhook '%s' ran skill '%s': %s", name, skill_name, str(result)[:300])
    notify_telegram(f"Webhook '{name}' fired.\n\n{result}")


@app.post("/webhooks/{name}")
async def webhooks_trigger(
    name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shelby_secret: str | None = Header(default=None),
):
    """Public trigger endpoint. External services POST here to run a bound skill.

    Auth is a shared secret, sent as the X-Shelby-Secret header (or a
    ?secret= query param for services that can't set custom headers). The
    request body is parsed as JSON and passed to the skill as kwargs; the
    skill runs in the background so this always responds immediately.
    """
    from ..webhooks import registry as webhook_registry

    hook = webhook_registry.get(name)
    if not hook:
        raise HTTPException(404, "Unknown webhook.")

    provided = x_shelby_secret or request.query_params.get("secret")
    if not webhook_registry.verify(name, provided or ""):
        raise HTTPException(401, "Invalid or missing webhook secret.")

    if agent is None or agent._skills is None:
        raise HTTPException(503, "Agent not ready")

    raw = await request.body()
    if len(raw) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Payload too large.")
    try:
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            payload = {"payload": payload}
    except json.JSONDecodeError:
        payload = {"raw": raw.decode("utf-8", errors="replace")}

    background_tasks.add_task(_run_webhook_skill, name, hook["skill"], payload)
    return {"status": "accepted", "webhook": name}


# ── Admin ────────────────────────────────────────────────────────────────────

@app.get("/admin/overview")
async def admin_overview():
    """Everything Shelby has persisted, in one place: memory, skills, tasks,
    webhooks, MCP connections, schema baselines, and usage/cost."""
    if agent is None:
        raise HTTPException(503, "Agent not ready")

    from .. import usage_tracker
    from ..dataquality import drift
    from ..mcp import list_servers
    from ..webhooks import registry as webhook_registry

    def _rows():
        stats = usage_tracker.get_stats()
        return {
            "memory": agent._notes.as_list(),
            "skills": agent._skills.list(),
            "tasks": agent._scheduler.list_tasks() if agent._scheduler else [],
            "webhooks": webhook_registry.list_webhooks(),
            "mcp_servers": list_servers(),
            "schema_baselines": drift.list_baselines(),
            "usage": {
                "total_calls": stats.get("total_calls", 0),
                "total_input_tokens": stats.get("total_input", 0),
                "total_output_tokens": stats.get("total_output", 0),
                "estimated_cost_usd": usage_tracker.estimated_cost_usd(stats),
                "by_model": stats.get("by_model", {}),
            },
            "rag_docs": rag_store.count() if rag_store else 0,
        }

    return await run_in_threadpool(_rows)


@app.get("/dataquality/graph")
async def dataquality_graph():
    """Topology of the LangGraph data-quality pipeline, plus a mermaid rendering."""
    from ..dataquality import graph as dq_graph
    def _build():
        d = dq_graph.describe_graph()
        d["mermaid"] = dq_graph.mermaid()
        return d
    return await run_in_threadpool(_build)


@app.get("/admin/graph")
async def admin_graph():
    """Node-link graph of the knowledge base: passages connected by semantic
    similarity, the same shape as an Obsidian graph view but computed from
    embeddings instead of authored links."""
    if rag_store is None:
        raise HTTPException(503, "Knowledge base not ready")
    return await run_in_threadpool(rag_store.graph_data)

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
    text, err = await run_in_threadpool(transcribe, data, mime)
    if not text:
        raise HTTPException(422, err or "Could not transcribe audio")
    return {"text": text}


@app.get("/stt/diag")
async def stt_diag():
    """Ping ElevenLabs to isolate outbound-network issues from audio-payload issues.

    Hits GET /v1/user (auth check, no file), so any failure is auth/network,
    not payload. Also reports the proxy env vars, since a Railway-configured
    HTTPS_PROXY is what would silently redirect our ElevenLabs request to an
    Anthropic gateway (and produce an Anthropic-shaped auth error).
    """
    key = os.getenv("ELEVENLABS_API_KEY")
    key_summary = None
    if key:
        key_summary = {"length": len(key), "prefix": key[:5], "suffix": key[-4:]}

    proxy_env = {
        var: os.getenv(var)
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
        if os.getenv(var)
    }

    async def _probe(via_proxy: bool):
        import httpx
        try:
            client_kwargs = {"timeout": 10}
            if not via_proxy:
                client_kwargs["proxy"] = None
                client_kwargs["trust_env"] = False
            async with httpx.AsyncClient(**client_kwargs) as client:
                r = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": key or ""},
                )
            return {
                "status": r.status_code,
                "server_header": r.headers.get("server"),
                "body_preview": r.text[:300],
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    with_proxy = await _probe(via_proxy=True)
    without_proxy = await _probe(via_proxy=False)

    return {
        "key_set": bool(key),
        "key_summary": key_summary,
        "proxy_env": proxy_env,
        "with_proxy": with_proxy,
        "without_proxy_bypass": without_proxy,
    }


# ── Voice output (TTS) ───────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str


@app.post("/tts")
async def tts(req: TTSRequest):
    """Synthesise Shelby's reply into an MP3 the browser can play."""
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise HTTPException(503, "Voice output unavailable (ELEVENLABS_API_KEY not set)")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Empty text")

    from ..tts.elevenlabs import synthesise_mp3
    audio, err = await run_in_threadpool(synthesise_mp3, text)
    if audio is None:
        raise HTTPException(502, err or "TTS failed")

    from fastapi.responses import Response
    return Response(content=audio, media_type="audio/mpeg")


# ── File input (upload a CSV → generic data-quality profile) ─────────────────

@app.post("/inspect/upload")
async def inspect_upload(file: UploadFile = File(...)):
    """Profile any uploaded CSV: rows, columns, duplicates, nulls, quality score."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")

    from ..dataquality.quickprofile import quick_profile_bytes
    return await run_in_threadpool(quick_profile_bytes, raw, file.filename or "upload.csv")


# ── File download ────────────────────────────────────────────────────────────

@app.get("/download/{relpath:path}")
async def download(relpath: str):
    """Serve a file Shelby generated. Restricted to files under the data dir."""
    from ..paths import DATA_DIR
    base = DATA_DIR.resolve()
    target = (base / relpath).resolve()
    if base != target and base not in target.parents:
        raise HTTPException(404, "Not found")
    if not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


# ── Chat ─────────────────────────────────────────────────────────────────────

def _agent_error_detail(exc: Exception) -> tuple[int, str]:
    """Map an agent/Anthropic exception to (http_status, message) for a clean response.

    Without this, any Claude API failure (bad key, exhausted rate limits across
    the whole fallback chain, a network blip) bubbles up as an unhandled
    exception and FastAPI turns it into a bare 500 with no explanation.
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return 502, "Claude API rejected the request: ANTHROPIC_API_KEY is missing or invalid."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return 502, "Claude API denied the request — check the API key's permissions."
    if isinstance(exc, anthropic.RateLimitError):
        return 429, "Rate limited on every model in the fallback chain. Try again shortly."
    if isinstance(exc, anthropic.APIStatusError):
        return 502, f"Claude API error ({exc.status_code}): {getattr(exc, 'message', str(exc))}"
    if isinstance(exc, anthropic.APIConnectionError):
        return 502, "Could not reach the Claude API from the server."
    return 500, f"Unexpected error: {exc}"


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if agent is None:
        raise HTTPException(503, "Agent not ready")
    if req.stream:
        raise HTTPException(400, "Use /chat/stream for streaming responses")

    messages = [m.model_dump() for m in req.messages]
    files: list[str] = []
    # Run synchronous agent in a thread so the event loop stays free
    try:
        reply, usage = await run_in_threadpool(
            lambda: agent.chat_with_usage(messages, collect_files=files)
        )
    except Exception as exc:
        log.exception("Chat request failed")
        status, detail = _agent_error_detail(exc)
        raise HTTPException(status, detail)

    from ..paths import DATA_DIR
    base = DATA_DIR.resolve()
    refs = []
    for f in files:
        try:
            rel = Path(f).resolve().relative_to(base)
            refs.append(FileRef(name=Path(f).name, url=f"/download/{rel}"))
        except ValueError:
            continue

    return ChatResponse(reply=reply, model=usage.model, files=refs)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if agent is None:
        raise HTTPException(503, "Agent not ready")
    messages = [m.model_dump() for m in req.messages]

    async def _gen() -> AsyncGenerator[str, None]:
        try:
            for chunk in agent.stream(messages):
                yield f"data: {chunk}\n\n"
        except Exception as exc:
            log.exception("Streaming chat failed")
            _, detail = _agent_error_detail(exc)
            yield f"data: [ERROR] {detail}\n\n"
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


@app.get("/rag/all")
async def rag_all(limit: int = 1000):
    """Get all documents in the knowledge base."""
    if rag_store is None:
        raise HTTPException(503, "RAG store not ready")
    docs = await run_in_threadpool(rag_store.get_all, limit)
    return {"documents": docs, "total": len(docs)}
