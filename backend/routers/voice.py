"""Voice endpoints: LiveKit token (optional) + VAD-based realtime WebSocket
conversation using Sarvam streaming STT/TTS. History is persisted via astream_ask.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

from fastapi import APIRouter, Depends, UploadFile, WebSocket, WebSocketDisconnect
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel

from agents.conversation_agent import ask, fast_voice_ask
from core.auth import ALGORITHM, get_current_user
from core.config import get_settings
from core.language import (
    call_opening_line,
    didnt_catch_reply,
    is_answerable_voice_question,
    is_usable_voice_transcript,
    requested_language,
)
from core.sarvam_client import speech_to_text
from core.sarvam_streaming import StreamingSttSession, synthesize_speech
from core.history import save_turn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

_KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")
SUPPORTED_VOICE_LANGS = ("en-IN", "kn-IN", "unknown")


class TokenRequest(BaseModel):
    session_id: str
    identity: str


def _normalize_lang(language_code: str) -> str:
    code = (language_code or "en-IN").strip()
    if code in SUPPORTED_VOICE_LANGS:
        return code
    lowered = code.lower()
    if lowered in ("en", "english"):
        return "en-IN"
    if lowered in ("kn", "kannada", "ಕನ್ನಡ"):
        return "kn-IN"
    return "en-IN"


def _stt_language(preference: str) -> str:
    """Always auto-detect: investigators mix Kannada and English in one call,
    so pinning STT to the UI toggle would mis-transcribe the other language."""
    return "unknown"


def _tts_language(transcript: str, answer: str, preference: str) -> str:
    if preference == "kn-IN":
        return "kn-IN"
    if _KANNADA_RE.search(transcript or "") or _KANNADA_RE.search(answer or ""):
        return "kn-IN"
    return "en-IN"


@router.get("/realtime/status")
def realtime_status(user: dict = Depends(get_current_user)) -> dict:
    settings = get_settings()
    livekit_ready = bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)
    return {
        "realtime": True,
        "vad": True,
        "streaming": True,
        "transport": "livekit" if livekit_ready else "websocket",
        "livekit_ready": livekit_ready,
    }


@router.post("/token", dependencies=[Depends(get_current_user)])
def livekit_token(req: TokenRequest) -> dict:
    settings = get_settings()
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return {"error": "LiveKit is not configured", "url": None, "token": None}
    from livekit.api import RoomAgentDispatch, RoomConfiguration

    # Explicit agent dispatch so LiveKit Cloud wakes our voice worker when the
    # investigator joins — required for a live call outside AppSail's WebSocket limits.
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(req.identity)
        .with_grants(VideoGrants(room_join=True, room=req.session_id, room_create=True))
        .with_room_config(
            RoomConfiguration(agents=[RoomAgentDispatch(agent_name="crime-ai-voice")])
        )
    )
    return {"token": token.to_jwt(), "url": settings.livekit_url}


@router.post("/ask", dependencies=[Depends(get_current_user)])
async def voice_ask(session_id: str, language_code: str = "en-IN", file: UploadFile = None) -> dict:
    """Legacy one-shot upload (debug). Prefer /voice/realtime streaming VAD call."""
    preference = _normalize_lang(language_code)
    audio_bytes = await file.read()
    content_type = file.content_type or "audio/wav"
    transcript = speech_to_text(
        audio_bytes,
        filename=file.filename,
        language_code=_stt_language(preference),
        content_type=content_type,
    )
    result = await ask(session_id, transcript)
    reply_language = _tts_language(transcript, result["answer"], preference)
    content_type, audio_b64 = await synthesize_speech(
        result["answer"], target_language_code=reply_language
    )
    return {
        "transcript": transcript,
        "answer": result["answer"],
        "tool": result.get("tool"),
        "query": result.get("query"),
        "language": reply_language,
        "content_type": content_type,
        "audio_base64": audio_b64,
    }


def _ws_auth(token: str | None) -> dict | None:
    if not token:
        return None
    import jwt

    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return {
            "username": payload.get("sub"),
            "role": payload.get("role"),
            "full_name": payload.get("full_name", ""),
        }
    except Exception:
        return None


@router.websocket("/realtime")
async def voice_realtime(websocket: WebSocket):
    """VAD-based realtime voice conversation (streaming).

    Client continuously streams PCM16 mono @ 16kHz.
    Sarvam streaming STT (saaras:v3) provides server-side VAD.
    On transcript: stream answer tokens + streaming TTS.
    Turns are persisted in chat_history.
    """
    await websocket.accept()
    params = websocket.query_params
    user = _ws_auth(params.get("token"))
    if user is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=4401)
        return

    session_id = params.get("session_id") or f"voice-{user['username']}"
    preference = _normalize_lang(params.get("language_code") or "en-IN")
    username = user.get("username")
    officer_name = user.get("full_name") or ""

    busy = asyncio.Lock()
    asr_reconnect_lock = asyncio.Lock()
    asr_ready = asyncio.Event()
    closed = False
    stt = StreamingSttSession(language_code=_stt_language(preference))
    # Buffer speech until a real pause — early VAD END cuts mid-sentence otherwise.
    utterance_parts: list[str] = []
    finalize_task: asyncio.Task | None = None
    speech_open = False
    UTTERANCE_PAUSE_SEC = 1.15

    async def send(obj: dict) -> None:
        if closed:
            return
        try:
            await websocket.send_json(obj)
        except Exception:
            pass

    def _merge_utterance(parts: list[str]) -> str:
        merged: list[str] = []
        for part in parts:
            piece = (part or "").strip()
            if not piece:
                continue
            if merged and piece.lower() == merged[-1].lower():
                continue
            # Prefer longer replacement when STT revises the same phrase.
            if merged and piece.lower().startswith(merged[-1].lower()):
                merged[-1] = piece
                continue
            if merged and merged[-1].lower().startswith(piece.lower()):
                continue
            merged.append(piece)
        return " ".join(merged).strip()

    async def _finalize_utterance() -> None:
        nonlocal finalize_task, speech_open
        finalize_task = None
        if busy.locked() or speech_open:
            return
        text = _merge_utterance(utterance_parts)
        utterance_parts.clear()
        if not text:
            return
        await send({"type": "status", "state": "thinking"})
        await handle_turn(text)

    def _schedule_finalize(delay: float = UTTERANCE_PAUSE_SEC) -> None:
        nonlocal finalize_task
        if finalize_task and not finalize_task.done():
            finalize_task.cancel()

        async def _wait() -> None:
            try:
                await asyncio.sleep(delay)
                await _finalize_utterance()
            except asyncio.CancelledError:
                return

        finalize_task = asyncio.create_task(_wait())

    async def handle_turn(transcript: str) -> None:
        nonlocal preference
        text = (transcript or "").strip()
        if not text:
            return
        if busy.locked():
            logger.info("Skipping overlapping transcript while busy: %r", text[:80])
            await send({
                "type": "status",
                "state": "busy",
                "message": "Still answering — pause a moment",
            })
            return
        if not is_usable_voice_transcript(text) or not is_answerable_voice_question(text):
            logger.info("Ignoring weak voice transcript: %r", text[:80])
            await send({"type": "transcript", "text": text, "rejected": True})
            reply_language = preference
            speak = didnt_catch_reply(reply_language)
            async with busy:
                await send({"type": "status", "state": "speaking"})
                await send({"type": "answer", "text": speak, "tool": "chitchat", "query": "", "rows": []})
                try:
                    content_type, audio_b64 = await asyncio.wait_for(
                        synthesize_speech(speak, target_language_code=reply_language, prefer_fast=True),
                        timeout=5.0,
                    )
                    await send({
                        "type": "audio_chunk",
                        "audio_base64": audio_b64,
                        "content_type": content_type,
                    })
                except Exception:
                    await send({"type": "speak_text", "text": speak, "language": reply_language})
                await send({"type": "audio_done"})
                await send({"type": "status", "state": "listening"})
            return

        async with busy:
            await send({"type": "status", "state": "thinking"})
            await send({"type": "transcript", "text": text})
            try:
                result = await asyncio.wait_for(
                    fast_voice_ask(
                        session_id,
                        text,
                        username=username,
                        officer_name=officer_name,
                        language_code=preference,
                    ),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Voice ask timed out for %r", text[:80])
                speak = (
                    "Taking too long Officer. Please ask a shorter question."
                    if preference != "kn-IN"
                    else "Time ಜಾಸ್ತಿ ಆಯ್ತು Officer. Short question ಕೇಳಿ."
                )
                await send({"type": "answer", "text": speak, "tool": "chitchat", "query": "", "rows": []})
                await send({"type": "status", "state": "speaking"})
                try:
                    content_type, audio_b64 = await asyncio.wait_for(
                        synthesize_speech(speak, target_language_code=preference, prefer_fast=True),
                        timeout=5.0,
                    )
                    await send({
                        "type": "audio_chunk",
                        "audio_base64": audio_b64,
                        "content_type": content_type,
                    })
                except Exception:
                    await send({"type": "speak_text", "text": speak, "language": preference})
                await send({"type": "audio_done"})
                await send({"type": "status", "state": "listening"})
                return
            except Exception as exc:
                logger.exception("Realtime ask failed")
                await send({"type": "error", "message": str(exc)})
                await send({"type": "status", "state": "listening"})
                return

            answer = result.get("answer") or ""
            speak = (result.get("speak") or answer or "").strip()
            tool = result.get("tool")
            query = result.get("query")
            reply_language = _tts_language(
                text, answer, result.get("language_code") or preference
            )
            # "Speak in Kannada" should hold for the rest of the call, not one turn.
            switched = requested_language(text)
            if switched:
                preference = switched
                reply_language = switched
            if not speak:
                speak = didnt_catch_reply(reply_language)

            await send({
                "type": "answer",
                "text": answer,
                "tool": tool,
                "query": query,
                "rows": result.get("rows") or [],
                "suggestions": result.get("suggestions") or [],
            })
            await send({"type": "status", "state": "speaking"})
            try:
                from core.elevenlabs_client import is_configured as eleven_ready

                timeout = 6.0 if eleven_ready() else 4.0
                content_type, audio_b64 = await asyncio.wait_for(
                    synthesize_speech(
                        speak,
                        target_language_code=reply_language,
                        prefer_fast=True,
                    ),
                    timeout=timeout,
                )
                await send({
                    "type": "audio_chunk",
                    "audio_base64": audio_b64,
                    "content_type": content_type,
                })
            except Exception:
                logger.info("Cloud TTS unavailable — using browser speech fallback")
                await send({"type": "speak_text", "text": speak, "language": reply_language})
            await send({"type": "audio_done"})
            await send({"type": "status", "state": "listening"})

    async def on_vad(signal: str) -> None:
        nonlocal speech_open, finalize_task
        await send({"type": "vad", "signal": signal})
        if signal == "START_SPEECH":
            speech_open = True
            # Speaker resumed — do not cut the previous fragment into an answer yet.
            if finalize_task and not finalize_task.done():
                finalize_task.cancel()
                finalize_task = None
            if not busy.locked():
                await send({"type": "status", "state": "listening"})
                await send({"type": "caption", "text": "Listening… keep speaking"})
        elif signal == "END_SPEECH":
            speech_open = False
            if not busy.locked():
                await send({"type": "status", "state": "listening"})
                await send({"type": "caption", "text": "Got a pause — waiting if you continue…"})
            # Grace period: officer may still be mid-thought.
            _schedule_finalize(UTTERANCE_PAUSE_SEC)

            async def _delayed_flush() -> None:
                await asyncio.sleep(0.4)
                if speech_open or closed:
                    return
                try:
                    await stt.flush()
                except Exception:
                    logger.debug("STT flush after END_SPEECH failed", exc_info=True)

            asyncio.create_task(_delayed_flush())

    async def on_transcript(text: str) -> None:
        piece = (text or "").strip()
        if not piece:
            return
        utterance_parts.append(piece)
        preview = _merge_utterance(utterance_parts)
        await send({"type": "transcript", "text": preview, "partial": True})
        # Keep extending the window while words keep arriving.
        if speech_open:
            if finalize_task and not finalize_task.done():
                finalize_task.cancel()
        else:
            _schedule_finalize(UTTERANCE_PAUSE_SEC)

    async def recover_stt() -> None:
        async with asr_reconnect_lock:
            if closed or asr_ready.is_set():
                return
            await send({"type": "status", "state": "reconnecting"})
            try:
                await stt.close()
                await asyncio.sleep(0.25)
                await stt.start()
                asr_ready.set()
                await send({"type": "status", "state": "listening"})
                logger.info("Sarvam streaming STT reconnected session=%s", session_id)
            except Exception:
                logger.exception("Could not reconnect Sarvam streaming STT")
                await send({
                    "type": "error",
                    "message": "Speech recognition disconnected. Please restart the call.",
                })

    async def on_stt_error(message: str) -> None:
        if closed:
            return
        logger.warning("Sarvam streaming STT error session=%s: %s", session_id, message)
        asr_ready.clear()
        asyncio.create_task(recover_stt())

    stt.on_vad = on_vad
    stt.on_transcript = on_transcript
    stt.on_error = on_stt_error

    greeting_text = call_opening_line(preference, officer_name=officer_name)

    # Kick off greeting TTS while STT connects — cuts call-start wait.
    greeting_task = asyncio.create_task(
        synthesize_speech(
            greeting_text,
            target_language_code=preference,
            prefer_fast=True,
        )
    )

    try:
        await stt.start()
        asr_ready.set()
    except Exception as exc:
        greeting_task.cancel()
        logger.exception("Failed to start Sarvam streaming STT")
        await send({"type": "error", "message": f"Could not start streaming STT: {exc}"})
        await websocket.close()
        return

    await send({
        "type": "ready",
        "session_id": session_id,
        "mode": "vad_streaming",
        "hint": "Stream PCM16 mono @ 16kHz; server VAD detects end of speech",
    })

    # Spoken greeting before listening — same short intro as chat.
    async with busy:
        await send({"type": "greeting", "text": greeting_text, "language": preference})
        await send({"type": "status", "state": "speaking"})
        try:
            content_type, audio_b64 = await asyncio.wait_for(greeting_task, timeout=8.0)
            await send({
                "type": "audio_chunk",
                "audio_base64": audio_b64,
                "content_type": content_type,
            })
        except Exception:
            logger.info("Greeting TTS failed — browser fallback")
            await send({
                "type": "speak_text",
                "text": greeting_text,
                "language": preference,
            })
        await send({"type": "audio_done"})
        try:
            save_turn(
                session_id,
                "(call started)",
                greeting_text,
                tool="chitchat",
                query=None,
                username=username,
                rows=[],
            )
        except Exception:
            logger.exception("Failed to store call greeting")
    await send({"type": "status", "state": "listening"})

    async def keepalive() -> None:
        while not closed:
            await asyncio.sleep(25)
            if not asr_ready.is_set():
                continue
            try:
                await stt.send_silence_keepalive()
            except Exception:
                asr_ready.clear()
                asyncio.create_task(recover_stt())

    ka_task = asyncio.create_task(keepalive())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "Invalid JSON"})
                continue

            kind = msg.get("type")
            if kind == "end":
                await send({"type": "ended"})
                break
            if kind == "ping":
                await send({"type": "pong"})
                try:
                    await stt.send_silence_keepalive()
                except Exception:
                    pass
                continue
            if kind == "audio_chunk":
                if not asr_ready.is_set():
                    continue
                b64 = msg.get("audio_base64") or ""
                if not b64:
                    continue
                try:
                    pcm = base64.b64decode(b64, validate=True)
                    await stt.send_pcm(pcm)
                except (ValueError, TypeError):
                    await send({"type": "error", "message": "Invalid microphone audio data."})
                except Exception:
                    # This is an upstream ASR disconnect, not a malformed browser chunk.
                    asr_ready.clear()
                    asyncio.create_task(recover_stt())
                continue
            if kind == "flush":
                await stt.flush()
                continue
            await send({"type": "error", "message": f"Unknown type: {kind}"})
    except WebSocketDisconnect:
        logger.info("Realtime voice disconnected session=%s", session_id)
    except Exception:
        logger.exception("Realtime voice socket error")
    finally:
        closed = True
        if finalize_task and not finalize_task.done():
            finalize_task.cancel()
        ka_task.cancel()
        await stt.close()
        try:
            await websocket.close()
        except Exception:
            pass
