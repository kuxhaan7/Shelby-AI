"""ElevenLabs TTS — returns OGG Opus bytes (Telegram voice-ready) or None."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)

DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
DEFAULT_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")


def synthesise(text: str) -> tuple[bytes | None, str | None]:
    """Convert text to OGG Opus bytes. Returns (audio_bytes, error_msg)."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None, "ELEVENLABS_API_KEY is not set in environment"

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
        if not mp3_bytes:
            return None, "ElevenLabs returned empty audio"
    except Exception as exc:
        log.error("ElevenLabs TTS API error: %s", exc)
        return None, f"ElevenLabs API error: {exc}"

    ogg, err = _mp3_to_ogg(mp3_bytes)
    return ogg, err


def _mp3_to_ogg(mp3_bytes: bytes) -> tuple[bytes | None, str | None]:
    """Convert MP3 bytes to OGG Opus. Returns (ogg_bytes, error_msg)."""
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
            err = result.stderr.decode(errors="replace")
            log.error("ffmpeg failed: %s", err)
            return None, f"ffmpeg conversion failed: {err[-200:]}"
        if not result.stdout:
            return None, "ffmpeg produced empty output"
        return result.stdout, None
    except FileNotFoundError:
        return None, "ffmpeg not found — not installed in this environment"
    except Exception as exc:
        return None, f"ffmpeg error: {exc}"
