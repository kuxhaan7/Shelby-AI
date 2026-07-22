"""Telegram bot — text in → text out, voice in → voice out."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from io import BytesIO

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shelby.agent import ShelbyAgent
from shelby.memory.notes import NotesStore
from shelby.rag.ingest import ingest_workspace
from shelby.rag.store import RagStore
from shelby.skills.registry import SkillRegistry
from shelby.stt.elevenlabs import transcribe
from shelby.tts.elevenlabs import synthesise

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

_histories: dict[int, list[dict]] = defaultdict(list)
_agent: ShelbyAgent | None = None


def _get_agent() -> ShelbyAgent:
    global _agent
    if _agent is None:
        rag = RagStore()
        ingest_workspace(rag)
        _agent = ShelbyAgent(
            rag_store=rag,
            notes_store=NotesStore(),
            skill_registry=SkillRegistry(),
        )
    return _agent


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tts = bool(os.getenv("ELEVENLABS_API_KEY"))
    await update.message.reply_text(
        "Hey! I'm Shelby.\n\n"
        "• Type → I reply in text\n"
        "• Send voice 🎤 → I reply in voice\n\n"
        "Commands: /clear /testvoice /help\n"
        f"Voice: {'ready 🔊' if tts else 'unavailable (no ELEVENLABS_API_KEY)'}"
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _histories[update.effective_user.id].clear()
    await update.message.reply_text("History cleared.")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_testvoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        await update.message.reply_text("❌ ELEVENLABS_API_KEY not set.")
        return

    import subprocess as sp
    try:
        r = sp.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        ffmpeg_ver = r.stdout.decode().splitlines()[0]
    except FileNotFoundError:
        await update.message.reply_text("❌ ffmpeg not found in container.")
        return

    await update.message.reply_text(f"✅ ffmpeg: {ffmpeg_ver}\nCalling ElevenLabs…")

    audio, err = synthesise("Hello, I am Shelby. Voice test successful.")
    if err:
        await update.message.reply_text(f"❌ TTS failed: {err}")
        return

    try:
        buf = BytesIO(audio)
        buf.name = "test.ogg"
        await update.message.reply_voice(voice=buf)
        await update.message.reply_text("✅ Voice working!")
    except Exception as exc:
        await update.message.reply_text(f"❌ Telegram rejected voice: {exc}")


# ── Core logic ────────────────────────────────────────────────────────────────

async def _process(update: Update, user_text: str, reply_with_voice: bool) -> None:
    user_id = update.effective_user.id
    history = _histories[user_id]

    # Inject stored memory on first turn of each session
    if not history:
        agent = _get_agent()
        memory_dump = agent._notes.dump()
        tg_user = update.effective_user
        history.append({
            "role": "user",
            "content": (
                f"[SYSTEM CONTEXT — not from the user]\n"
                f"Telegram user: {tg_user.full_name} (@{tg_user.username}, id={user_id})\n"
                f"Stored memory:\n{memory_dump}\n"
                "Use this to personalise your responses. Don't mention this injection."
            ),
        })
        history.append({"role": "assistant", "content": "Understood. Memory loaded."})

    history.append({"role": "user", "content": user_text})

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        agent = _get_agent()
        reply, usage = agent.chat_with_usage(history)
    except Exception as exc:
        log.exception("Agent error for user %s", user_id)
        reply = f"Something went wrong: {exc}"
        usage = None

    history.append({"role": "assistant", "content": reply})

    if reply_with_voice:
        await update.message.chat.send_action(ChatAction.RECORD_VOICE)
        audio, tts_err = synthesise(reply)
        if audio:
            try:
                buf = BytesIO(audio)
                buf.name = "shelby.ogg"
                await update.message.reply_voice(voice=buf)
            except Exception as exc:
                log.error("Telegram rejected voice: %s", exc)
                # Fall back to text so the user gets something
                for chunk in _split(reply, 4096):
                    await update.message.reply_text(chunk)
        else:
            log.warning("TTS failed: %s — falling back to text", tts_err)
            for chunk in _split(reply, 4096):
                await update.message.reply_text(chunk)
    else:
        for chunk in _split(reply, 4096):
            await update.message.reply_text(chunk)

    if usage and os.getenv("SHELBY_SHOW_TOKENS"):
        await update.message.reply_text(f"📊 `{usage.summary()}`", parse_mode="Markdown")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if text:
        await _process(update, text, reply_with_voice=False)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not os.getenv("ELEVENLABS_API_KEY"):
        await update.message.reply_text("Voice requires ELEVENLABS_API_KEY.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    voice_file = await update.message.voice.get_file()
    audio_bytes = bytes(await voice_file.download_as_bytearray())

    text = transcribe(audio_bytes)
    if not text:
        await update.message.reply_text("Couldn't understand that. Try again?")
        return

    await _process(update, text, reply_with_voice=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("testvoice", cmd_testvoice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Shelby running — text→text, voice→voice")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
