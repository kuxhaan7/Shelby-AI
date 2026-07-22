"""ElevenLabs TTS — returns OGG Opus bytes (Telegram voice-ready) or None."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)

DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
DEFAULT_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")


def synthesise(text: str) -> bytes | None:
    """Convert text to OGG Opus bytes for Telegram reply_voice. Returns None on failure."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        log.debug("ELEVENLABS_API_KEY not set — TTS skipped.")
        return None

    max_chars = int(os.getenv("ELEVENLABS_MAX_CHARS", "3000"))
    if len(text) > max_chars:
        text = text[:max_chars] + "…"

    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        mp3_chunks = client.text_to_speech.convert(
            voice_id=DEFAULT_VOICE_ID,
            text=text,
            model_id=DEFAULT_MODEL,
            output_format="mp3_44100_128",
        )
        mp3_bytes = b"".join(mp3_chunks)
    except Exception as exc:
        log.error("ElevenLabs TTS API error: %s", exc)
        return None

    return _mp3_to_ogg(mp3_bytes)


def _mp3_to_ogg(mp3_bytes: bytes) -> bytes | None:
    """Convert MP3 bytes to OGG Opus bytes using ffmpeg (required by Telegram sendVoice)."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", "pipe:0",
                "-c:a", "libopus",
                "-b:a", "64k",
                "-vbr", "on",
                "-f", "ogg",
                "pipe:1",
            ],
            input=mp3_bytes,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.error("ffmpeg conversion failed: %s", result.stderr.decode())
            return None
        return result.stdout
    except FileNotFoundError:
        log.error("ffmpeg not found — cannot convert MP3 to OGG. Install ffmpeg.")
        return None
    except Exception as exc:
        log.error("ffmpeg error: %s", exc)
        return None
