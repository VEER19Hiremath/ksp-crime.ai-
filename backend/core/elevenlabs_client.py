"""ElevenLabs TTS — natural voice for Crime AI realtime replies.

Docs: https://elevenlabs.io/docs/api-reference/text-to-speech/convert
"""
from __future__ import annotations

import logging

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def _require_key() -> str:
    settings = get_settings()
    key = (settings.elevenlabs_api_key or "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return key


def is_configured() -> bool:
    return bool((get_settings().elevenlabs_api_key or "").strip())


def text_to_speech(
    text: str,
    *,
    language_code: str = "en-IN",
    voice_id: str | None = None,
    model_id: str | None = None,
) -> bytes:
    """Return MP3 audio bytes from ElevenLabs."""
    settings = get_settings()
    speak = (text or "").strip()
    if not speak:
        raise ValueError("Empty text for ElevenLabs TTS")

    # Cap length — voice replies should stay short anyway.
    if len(speak) > 900:
        speak = speak[:880].rsplit(" ", 1)[0] + "..."

    voice = voice_id or settings.elevenlabs_voice_id or "EXAVITQu4vr4xnSDxMaL"
    is_kannada = language_code == "kn-IN" or any("\u0C80" <= ch <= "\u0CFF" for ch in speak)
    # multilingual_v2 for natural English; Kannada must use Sarvam (not ElevenLabs).
    if model_id:
        model = model_id
    elif is_kannada:
        model = settings.elevenlabs_model_kn or "eleven_v3"
    else:
        model = settings.elevenlabs_model or "eleven_multilingual_v2"

    url = ELEVEN_TTS_URL.format(voice_id=voice)
    # Higher bitrate; mild streaming latency opt so voice replies start sooner.
    params = {
        "output_format": settings.elevenlabs_output_format or "mp3_44100_128",
        "optimize_streaming_latency": 3,
    }
    # Conversational delivery without flat IVR.
    voice_settings = {
        "stability": 0.38,
        "similarity_boost": 0.75,
        "style": 0.28,
        "use_speaker_boost": True,
        "speed": 1.05,
    }
    if model.startswith("eleven_v3"):
        voice_settings = {
            "stability": 0.45,
            "similarity_boost": 0.75,
            "use_speaker_boost": True,
        }
    payload = {
        "text": speak,
        "model_id": model,
        "voice_settings": voice_settings,
    }
    headers = {
        "xi-api-key": _require_key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = client.post(url, params=params, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.error(
                "ElevenLabs TTS failed status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:300],
            )
            resp.raise_for_status()
        return resp.content
