"""LiveKit Agents TTS plugin backed by Sarvam's batch text-to-speech REST API,
requesting raw linear16 PCM directly (mime_type="audio/pcm") so LiveKit's
AudioEmitter can push it straight onto the room's audio track with no decode
step. Implemented as a ChunkedStream (capabilities.streaming=False); AgentSession
auto-wraps this in tts.StreamAdapter so LLM output is still synthesized
sentence-by-sentence rather than waiting for the whole answer."""
import asyncio

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, tts
from livekit.agents.utils import shortuuid

from core.config import get_settings
from core.sarvam_client import text_to_speech

_LANGUAGE_MAP = {"en": "en-IN", "kn": "kn-IN"}
SAMPLE_RATE = 16000
NUM_CHANNELS = 1


class SarvamTTS(tts.TTS):
    def __init__(self, *, language: str = "en-IN", speaker: str | None = None) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        settings = get_settings()
        self._language = _LANGUAGE_MAP.get(language, language)
        # bulbul:v3 rejects legacy speakers like "anushka" — use configured voice.
        self._speaker = speaker or settings.sarvam_tts_speaker or "simran"

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> "SarvamChunkedStream":
        return SarvamChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class SarvamChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        sarvam_tts: SarvamTTS = self._tts  # type: ignore[assignment]
        try:
            audio_bytes = await asyncio.to_thread(
                text_to_speech,
                self._input_text,
                target_language_code=sarvam_tts._language,
                speaker=sarvam_tts._speaker,
                output_audio_codec="linear16",
                speech_sample_rate=SAMPLE_RATE,
            )
        except Exception as e:
            raise APIConnectionError("Sarvam TTS request failed") from e

        if not audio_bytes:
            raise APIConnectionError("Sarvam TTS returned empty audio")

        output_emitter.initialize(
            request_id=shortuuid("sarvam_tts_"),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
            frame_size_ms=200,
        )
        # Prefer one push of the full clip — smoother than many tiny frames.
        output_emitter.push(audio_bytes)
        output_emitter.end_input()
