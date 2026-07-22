"""ElevenLabs text-to-speech — returns MP3 bytes or None if unavailable."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Default to "Rachel" — a clear, natural voice. Override via env var.
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
DEFAULT_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")


def synthesise(text: str) -> bytes | None:
    """Convert text to speech. Returns MP3 bytes, or None if TTS is disabled/fails."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None

    # Trim very long responses — ElevenLabs charges per character
    max_chars = int(os.getenv("ELEVENLABS_MAX_CHARS", "3000"))
    if len(text) > max_chars:
        text = text[:max_chars] + "…"

    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        audio = client.text_to_speech.convert(
            voice_id=DEFAULT_VOICE_ID,
            text=text,
            model_id=DEFAULT_MODEL,
            output_format="mp3_44100_128",
        )
        return b"".join(audio)
    except Exception as exc:
        log.warning("ElevenLabs TTS failed: %s", exc)
        return None
