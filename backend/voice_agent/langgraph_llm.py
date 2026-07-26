"""Wraps our LangGraph conversation agent as a LiveKit Agents LLM.

Emits the full spoken answer in one chunk (not token-by-token) so Sarvam TTS
synthesizes a single smooth utterance instead of choppy per-sentence clips.
"""
import logging

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, llm

from agents.conversation_agent import astream_ask

logger = logging.getLogger("voice_agent.langgraph_llm")


class LangGraphLLM(llm.LLM):
    def __init__(self, *, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools=None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=None,
        tool_choice=None,
        extra_kwargs=None,
    ) -> "LangGraphLLMStream":
        return LangGraphLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            session_id=self._session_id,
        )


class LangGraphLLMStream(llm.LLMStream):
    def __init__(
        self, llm_: LangGraphLLM, *, chat_ctx, tools, conn_options, session_id: str
    ) -> None:
        super().__init__(llm_, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._session_id = session_id

    async def _run(self) -> None:
        question = (self._latest_user_text() or "").strip()
        # Ignore VAD false-starts / empty STT so we don't "answer" silence.
        if len(question) < 2:
            logger.info("skipping empty/short voice transcript: %r", question)
            return

        request_id = "langgraph"
        parts: list[str] = []
        async for event in astream_ask(self._session_id, question):
            if "token" in event:
                parts.append(str(event["token"] or ""))
            else:
                logger.info(
                    "voice turn audit trail: tool=%s query=%s",
                    event.get("tool"),
                    event.get("query"),
                )

        answer = "".join(parts).strip()
        if not answer:
            answer = "I did not catch that clearly. Please ask again."

        # One chunk → one TTS synthesis → smoother speech.
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id=request_id,
                delta=llm.ChoiceDelta(role="assistant", content=answer),
            )
        )

    def _latest_user_text(self) -> str:
        for item in reversed(self.chat_ctx.items):
            if getattr(item, "role", None) == "user":
                return item.text_content or ""
        return ""
