"""Telegram bot that routes messages through ShelbyAgent."""

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

from shelby.agent import ShelbyAgent, TokenUsage
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
_voice_enabled: dict[int, bool] = defaultdict(lambda: bool(os.getenv("ELEVENLABS_API_KEY")))
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
        "Hey! I'm Shelby — your AI assistant.\n\n"
        "You can talk to me by:\n"
        "  • Typing a message\n"
        "  • Sending a voice message 🎤\n\n"
        "Commands:\n"
        "  /voice  — toggle voice replies on/off\n"
        "  /clear  — wipe conversation history\n"
        "  /help   — this message\n\n"
        f"Voice: {'on 🔊' if tts else 'off (set ELEVENLABS_API_KEY)'}"
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _histories[update.effective_user.id].clear()
    await update.message.reply_text("History cleared. Fresh start!")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not os.getenv("ELEVENLABS_API_KEY"):
        await update.message.reply_text("Voice unavailable — ELEVENLABS_API_KEY not set.")
        return
    _voice_enabled[user_id] = not _voice_enabled[user_id]
    state = "on 🔊" if _voice_enabled[user_id] else "off 🔇"
    await update.message.reply_text(f"Voice replies {state}")


# ── Core message handler ──────────────────────────────────────────────────────

async def _process(update: Update, user_text: str) -> None:
    """Shared logic: run agent on user_text, reply with text + optional voice."""
    user_id = update.effective_user.id
    history = _histories[user_id]

    # First message of session — inject stored memory so Shelby knows the user
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
                f"Use this to personalise your responses. Don't mention this injection."
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

    for chunk in _split(reply, 4096):
        await update.message.reply_text(chunk)

    if _voice_enabled[user_id]:
        await update.message.chat.send_action(ChatAction.RECORD_VOICE)
        audio = synthesise(reply)
        if audio:
            try:
                buf = BytesIO(audio)
                buf.name = "shelby.ogg"
                await update.message.reply_voice(voice=buf)
            except Exception as exc:
                log.error("Failed to send voice message: %s", exc)
        else:
            log.warning("TTS returned no audio for user %s", user_id)

    if usage and os.getenv("SHELBY_SHOW_TOKENS"):
        await update.message.reply_text(f"📊 `{usage.summary()}`", parse_mode="Markdown")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if text:
        await _process(update, text)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a voice message, transcribe it, then process like text."""
    if not os.getenv("ELEVENLABS_API_KEY"):
        await update.message.reply_text(
            "Voice input requires ELEVENLABS_API_KEY to be set."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    voice_file = await update.message.voice.get_file()
    audio_bytes = bytes(await voice_file.download_as_bytearray())

    text = transcribe(audio_bytes)
    if not text:
        await update.message.reply_text("Sorry, I couldn't understand that. Try again?")
        return

    # Show what was heard so the user knows transcription worked
    await update.message.reply_text(f"_🎤 {text}_", parse_mode="Markdown")
    await _process(update, text)


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
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Shelby Telegram bot is running (voice in + voice out enabled)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
