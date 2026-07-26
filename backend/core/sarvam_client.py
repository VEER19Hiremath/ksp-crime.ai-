"""Thin wrappers around Sarvam's STT (saarika) and TTS (bulbul) REST APIs.
Docs: https://docs.sarvam.ai — used directly (no LiveKit-Sarvam plugin exists yet),
so these also back the LiveKit STT/TTS plugin classes in voice_agent/.
"""
import base64

import requests

from core.config import get_settings

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


def _require_key() -> str:
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError(
            "SARVAM_API_KEY is not set — copy backend/.env.example to backend/.env"
        )
    return settings.sarvam_api_key


def speech_to_text(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    language_code: str = "unknown",
    content_type: str = "audio/wav",
) -> str:
    """language_code: 'en-IN', 'kn-IN', or 'unknown' to let Sarvam auto-detect.
    content_type must be set explicitly — Sarvam rejects the upload as
    "Invalid file type: None" if the multipart part has no Content-Type."""
    settings = get_settings()
    resp = requests.post(
        SARVAM_STT_URL,
        headers={"api-subscription-key": _require_key()},
        data={"model": settings.sarvam_stt_model, "language_code": language_code},
        files={"file": (filename, audio_bytes, content_type)},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["transcript"]


def text_to_speech(
    text: str,
    target_language_code: str = "en-IN",
    speaker: str | None = None,
    output_audio_codec: str = "wav",
    speech_sample_rate: int | None = None,
    pace: float | None = None,
) -> bytes:
    """Returns raw audio bytes. Default voice is Simran on bulbul:v3."""
    settings = get_settings()
    model = settings.sarvam_tts_model or "bulbul:v3"
    voice = speaker or settings.sarvam_tts_speaker or "simran"
    payload = {
        "text": text,
        "target_language_code": target_language_code,
        "model": model,
        "speaker": voice,
        "output_audio_codec": output_audio_codec,
    }
    # Natural conversational pace for Indic briefing voice.
    if pace is not None:
        payload["pace"] = pace
    elif model.startswith("bulbul:v3"):
        payload["pace"] = 0.95
        payload["temperature"] = 0.75
    if speech_sample_rate is not None:
        payload["speech_sample_rate"] = speech_sample_rate
    resp = requests.post(
        SARVAM_TTS_URL,
        headers={
            "api-subscription-key": _require_key(),
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=45,
    )
    resp.raise_for_status()
    audio_b64_chunks = resp.json()["audios"]
    return b"".join(base64.b64decode(chunk) for chunk in audio_b64_chunks)
