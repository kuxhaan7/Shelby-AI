"""ElevenLabs Scribe STT — transcribe voice bytes to text."""

from __future__ import annotations

import logging
import os
from io import BytesIO

log = logging.getLogger(__name__)


# Map browser/Telegram mime types to a filename ElevenLabs accepts.
_EXT_BY_MIME = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> tuple[str | None, str | None]:
    """Transcribe audio bytes to text. Returns (text, error_msg).

    text is None on any failure; error_msg carries the reason so the caller
    can surface it instead of a generic 'could not transcribe'.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None, "ELEVENLABS_API_KEY is not set in environment"

    # Strip any codecs parameter (e.g. "audio/webm;codecs=opus") so the mime
    # lookup and the ElevenLabs Content-Type header both match.
    base_mime = (mime_type or "audio/webm").split(";", 1)[0].strip().lower()
    ext = _EXT_BY_MIME.get(base_mime, "webm")

    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        buf = BytesIO(audio_bytes)
        filename = f"voice.{ext}"
        buf.name = filename
        result = client.speech_to_text.convert(
            file=(filename, buf, base_mime),
            model_id="scribe_v1",
        )
        text = (result.text or "").strip()
        if not text:
            return None, "ElevenLabs returned an empty transcript (recording may be silent or too short)"
        return text, None
    except Exception as exc:
        log.warning("ElevenLabs STT failed: %s", exc)
        return None, f"ElevenLabs STT error: {exc}"
