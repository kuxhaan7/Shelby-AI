"""Telegram bot that routes messages through ShelbyAgent."""

from __future__ import annotations

import logging
import os
from collections import defaultdict

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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Per-user conversation history: {user_id: [{"role": ..., "content": ...}]}
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


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey! I'm Shelby — your Claude-powered assistant.\n"
        "Just send me a message to get started.\n\n"
        "Commands:\n"
        "  /clear — wipe your conversation history\n"
        "  /help  — show this message"
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    _histories[user_id].clear()
    await update.message.reply_text("History cleared. Fresh start!")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text:
        return

    history = _histories[user_id]
    history.append({"role": "user", "content": text})

    # Show typing indicator while Shelby thinks
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

    if usage and os.getenv("SHELBY_SHOW_TOKENS"):
        await update.message.reply_text(
            f"📊 `{usage.summary()}`", parse_mode="Markdown"
        )


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Shelby Telegram bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
