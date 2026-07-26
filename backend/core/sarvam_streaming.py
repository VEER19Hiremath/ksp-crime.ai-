"""Sarvam streaming STT (VAD) + reliable TTS for realtime voice calls."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import struct
from typing import AsyncIterator, Awaitable, Callable

from sarvamai import AsyncSarvamAI

from core.config import get_settings
from core.sarvam_client import text_to_speech

logger = logging.getLogger(__name__)

_KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")


def _client() -> AsyncSarvamAI:
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not set")
    return AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)


def _lang(preference: str) -> str:
    if preference in ("kn-IN", "en-IN", "unknown"):
        return preference
    return "unknown"


def pcm_s16le_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw PCM (s16le) in a minimal WAV header."""
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm


def speakable_text(text: str, *, max_chars: int = 700) -> str:
    """Strip markup / odd punctuation so Sarvam TTS accepts the string."""
    t = (text or "").strip()
    t = re.sub(r"[*_`#]+", " ", t)
    t = t.replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        t = t[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    if not t:
        return "I did not catch that. Please ask again."
    if not re.search(r"[A-Za-z\u0C80-\u0CFF]", t):
        return "I found some results, but could not read them aloud. Please check the screen."
    return t


def pick_tts_language(text: str, preference: str) -> str:
    # Kannada TTS only when the line has Kannada script.
    # Latin mix like "Namaskara Officer" sounds natural on English TTS.
    _ = preference
    if _KANNADA_RE.search(text or ""):
        return "kn-IN"
    return "en-IN"


async def synthesize_speech(
    text: str,
    *,
    target_language_code: str = "en-IN",
    speaker: str | None = None,
    prefer_fast: bool = False,
) -> tuple[str, str]:
    """Return (content_type, audio_base64).

    English → ElevenLabs multilingual_v2 (natural voice).
    Kannada → Sarvam bulbul only (native Indic voice). ElevenLabs
    sounds like a foreigner reading Kannada, so it is never used for kn-IN.
    """
    from core import elevenlabs_client

    settings = get_settings()
    # Keep Kannada lines short so Sarvam stays snappy on voice calls.
    speak = speakable_text(text, max_chars=120 if prefer_fast else 180)
    lang = pick_tts_language(speak, target_language_code)
    voice = speaker or settings.sarvam_tts_speaker or "simran"

    if lang == "kn-IN":
        # Never fall back to ElevenLabs for Kannada — it sounds foreign.
        audio = await asyncio.to_thread(text_to_speech, speak, lang, voice)
        return "audio/wav", base64.b64encode(audio).decode("ascii")

    if elevenlabs_client.is_configured():
        try:
            audio = await asyncio.to_thread(
                elevenlabs_client.text_to_speech,
                speak,
                language_code=lang,
            )
            return "audio/mpeg", base64.b64encode(audio).decode("ascii")
        except Exception:
            logger.exception("ElevenLabs TTS failed; falling back to Sarvam")

    audio = await asyncio.to_thread(text_to_speech, speak, lang, voice)
    return "audio/wav", base64.b64encode(audio).decode("ascii")


async def stream_tts_audio(
    text: str,
    *,
    target_language_code: str = "en-IN",
    speaker: str | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Compat wrapper — yields a single complete WAV."""
    ctype, b64 = await synthesize_speech(
        text, target_language_code=target_language_code, speaker=speaker
    )
    yield ctype, b64


OnVad = Callable[[str], Awaitable[None]]
OnTranscript = Callable[[str], Awaitable[None]]


class StreamingSttSession:
    """Keeps a Sarvam STT websocket open, forwards PCM, surfaces VAD + transcripts."""

    def __init__(self, language_code: str = "unknown") -> None:
        self.language_code = _lang(language_code)
        self._client = _client()
        self._ws = None
        self._ctx = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._failed = False
        self.on_vad: OnVad | None = None
        self.on_transcript: OnTranscript | None = None
        self.on_error: Callable[[str], Awaitable[None]] | None = None

    async def start(self) -> None:
        self._closed = False
        self._failed = False
        model = "saaras:v3"
        self._ctx = self._client.speech_to_text_streaming.connect(
            language_code=self.language_code,
            model=model,
            mode="transcribe",
            sample_rate="16000",
            # Browser sends signed 16-bit little-endian PCM, not complete WAV files.
            input_audio_codec="pcm_s16le",
            # Stay in "speech" through short pauses mid-sentence (thinking gaps).
            high_vad_sensitivity="true",
            positive_speech_threshold="0.45",
            negative_speech_threshold="0.25",
            min_speech_frames="5",
            first_turn_min_speech_frames="6",
            # Need a longer stretch of silence before END_SPEECH (~0.6–0.8s).
            negative_frames_count="28",
            negative_frames_window="40",
            pre_speech_pad_frames="8",
            vad_signals="true",
            flush_signal="true",
        )
        self._ws = await self._ctx.__aenter__()
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("Sarvam streaming STT connected (model=%s lang=%s)", model, self.language_code)

    async def send_pcm(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        if self._closed or self._failed or self._ws is None:
            raise RuntimeError("ASR stream is unavailable")
        if len(pcm_bytes) % 2:
            pcm_bytes = pcm_bytes[:-1]
        if not pcm_bytes:
            return
        # input_audio_codec=pcm_s16le tells Sarvam these bytes have no WAV header.
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        try:
            await self._ws.transcribe(
                audio_b64,
                encoding="audio/wav",
                sample_rate=16000,
            )
        except Exception:
            self._failed = True
            raise

    async def send_silence_keepalive(self) -> None:
        silence = b"\x00\x00" * 1600
        await self.send_pcm(silence)

    async def flush(self) -> None:
        if self._ws is not None:
            await self._ws.flush()

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        while not self._closed:
            try:
                msg = await self._ws.recv()
            except Exception as exc:
                self._failed = True
                if not self._closed and self.on_error:
                    await self.on_error(f"STT stream closed: {exc}")
                break
            try:
                msg_type = getattr(msg, "type", None)
                data = getattr(msg, "data", None)
                if msg_type == "events" or (data is not None and getattr(data, "signal_type", None)):
                    signal = getattr(data, "signal_type", None)
                    if signal and self.on_vad:
                        await self.on_vad(signal)
                elif msg_type == "data" or (data is not None and getattr(data, "transcript", None) is not None):
                    transcript = (getattr(data, "transcript", None) or "").strip()
                    # Prefer final / committed transcripts when the SDK marks them.
                    is_final = getattr(data, "is_final", None)
                    if is_final is False:
                        continue
                    prob = getattr(data, "language_probability", None)
                    if prob is not None:
                        try:
                            if float(prob) < 0.35:
                                logger.info("Dropping low-confidence transcript prob=%.2f text=%r", float(prob), transcript[:80])
                                continue
                        except (TypeError, ValueError):
                            pass
                    if transcript and self.on_transcript:
                        await self.on_transcript(transcript)
                elif msg_type == "error":
                    self._failed = True
                    if self.on_error:
                        await self.on_error(str(data))
            except Exception:
                logger.exception("Error handling STT streaming message")

    async def close(self) -> None:
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                logger.exception("Error closing Sarvam STT stream")
            self._ctx = None
            self._ws = None
