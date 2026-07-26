"""LiveKit Agents STT plugin backed by Sarvam's batch speech-to-text REST API.

Implemented via `_recognize_impl` (batch, capabilities.streaming=False) rather
than a bidirectional websocket stream: Sarvam's STT response schema doesn't
expose interim/partial transcripts anyway (confirmed against their docs), so a
true streaming implementation wouldn't buy responsiveness over LiveKit's own
VAD segmenting speech into utterances and calling recognize() once per
utterance (via stt.StreamAdapter, wired automatically by AgentSession when
capabilities.streaming=False)."""
import asyncio
import logging

from livekit.agents import APIConnectionError, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given
from livekit.agents.utils.audio import AudioBuffer, combine_frames as combine_audio_frames

from core.sarvam_client import speech_to_text

logger = logging.getLogger("voice_agent.sarvam_stt")

_LANGUAGE_MAP = {"en": "en-IN", "kn": "kn-IN"}


class SarvamSTT(stt.STT):
    def __init__(self, language: str = "unknown") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._language = language

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options,
    ) -> stt.SpeechEvent:
        frame = combine_audio_frames(buffer)
        wav_bytes = frame.to_wav_bytes()
        language_code = (
            _LANGUAGE_MAP.get(language, language) if is_given(language) else self._language
        )
        if language_code in ("en", "english"):
            language_code = "en-IN"
        elif language_code in ("kn", "kannada"):
            language_code = "kn-IN"
        elif not language_code:
            language_code = self._language or "unknown"

        try:
            transcript = await asyncio.to_thread(
                speech_to_text,
                wav_bytes,
                filename="utterance.wav",
                language_code=language_code,
                content_type="audio/wav",
            )
            transcript = (transcript or "").strip()
            logger.info("STT (%s): %r", language_code, transcript[:120])
        except Exception as e:
            raise APIConnectionError("Sarvam STT request failed") from e

        # Drop pure noise / empty finals so the agent does not "answer" silence.
        if len(transcript) < 2:
            transcript = ""

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=language_code or "en-IN", text=transcript)],
        )
