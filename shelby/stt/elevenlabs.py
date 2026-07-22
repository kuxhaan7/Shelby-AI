"""ElevenLabs Scribe STT — transcribe voice bytes to text."""

from __future__ import annotations

import logging
import os
from io import BytesIO

log = logging.getLogger(__name__)


def transcribe(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """Transcribe audio bytes to text. Returns None if STT is unavailable/fails."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        buf = BytesIO(audio_bytes)
        buf.name = "voice.ogg"
        result = client.speech_to_text.convert(
            file=(buf.name, buf, mime_type),
            model_id="scribe_v1",
        )
        return result.text.strip() if result.text else None
    except Exception as exc:
        log.warning("ElevenLabs STT failed: %s", exc)
        return None
