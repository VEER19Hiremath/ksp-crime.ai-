"""LiveKit Agents worker: realtime voice for investigators.

Mic -> Silero VAD -> Sarvam STT -> LangGraph -> Sarvam TTS back into the room.
"""
import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    EndpointingOptions,
    JobContext,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    room_io,
)
from livekit.agents.inference import VAD

from core.language import call_opening_line
from voice_agent.langgraph_llm import LangGraphLLM
from voice_agent.sarvam_stt import SarvamSTT
from voice_agent.sarvam_tts import SarvamTTS

load_dotenv()

logger = logging.getLogger("voice_agent.worker")

INSTRUCTIONS = (
    "You are Crime AI, a voice assistant for Karnataka Police investigators. "
    "Keep spoken answers short and clear."
)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    session_id = ctx.room.name or "voice-session"
    logger.info("voice agent joining room %s", session_id)

    participant = await ctx.wait_for_participant()
    logger.info("participant ready: %s", participant.identity)

    room_options = room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(sample_rate=16000, num_channels=1),
        audio_output=room_io.AudioOutputOptions(sample_rate=16000, num_channels=1),
        close_on_disconnect=False,
    )

    # Sarvam batch STT is slow (~2–5s). Default min_delay=0.5 commits the turn
    # before the transcript arrives → "not listening" / cut-off phrases.
    session = AgentSession(
        stt=SarvamSTT(language="unknown"),
        vad=VAD(
            model="silero",
            min_speech_duration=0.2,
            min_silence_duration=1.35,
            activation_threshold=0.4,
            prefix_padding_duration=0.6,
        ),
        llm=LangGraphLLM(session_id=session_id),
        tts=SarvamTTS(language="en-IN"),
        turn_handling=TurnHandlingOptions(
            endpointing=EndpointingOptions(
                min_delay=2.8,
                max_delay=8.0,
            ),
        ),
        min_interruption_duration=0.6,
        min_interruption_words=2,
        allow_interruptions=True,
    )
    await session.start(
        agent=Agent(instructions=INSTRUCTIONS),
        room=ctx.room,
        room_options=room_options,
    )

    greeting = call_opening_line("en-IN", officer_name=None)
    logger.info("speaking greeting: %s", greeting)
    try:
        await ctx.room.local_participant.publish_data(
            greeting.encode("utf-8"),
            reliable=True,
            topic="lk.chat",
        )
    except Exception:
        logger.exception("failed to publish greeting caption")

    try:
        handle = session.say(greeting, allow_interruptions=True)
        await handle.wait_for_playout()
        logger.info("greeting playout done (interrupted=%s)", handle.interrupted)
    except Exception:
        logger.exception("greeting TTS/playout failed")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="crime-ai-voice",
            load_fnc=lambda: 0.1,
            load_threshold=0.9,
        )
    )
