"""Proactive outbound notifications — Shelby messaging you, not the other way round.

Used anywhere Shelby needs to push something without being asked: a webhook
result, the scheduler's heartbeat, a scheduled task's output. Currently
Telegram only. Requires both TELEGRAM_BOT_TOKEN (already set for the bot
itself) and TELEGRAM_NOTIFY_CHAT_ID (the chat to push into — message the bot
/id to get yours). Silently does nothing if either is unset, so this is
always optional, never a hard dependency.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def notify_telegram(text: str) -> None:
    """Best-effort push of *text* to the configured Telegram chat. No-ops if unset."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=10,
        )
    except Exception:
        log.exception("Failed to push Telegram notification")
