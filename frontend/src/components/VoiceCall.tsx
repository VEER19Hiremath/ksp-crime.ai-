"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { voiceRealtimeWsUrl, type ChatTurn } from "@/lib/api";

type VoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "busy"
  | "error";

type Props = {
  sessionId: string;
  languageCode: "en-IN" | "kn-IN";
  onTurn: (turn: ChatTurn) => void;
  onError?: (message: string) => void;
  onCallActiveChange?: (active: boolean) => void;
};

function floatTo16BitPCM(float32: Float32Array): Int16Array {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function downsampleTo16k(input: Float32Array, inputRate: number): Float32Array {
  if (inputRate === 16000) return input;
  const ratio = inputRate / 16000;
  const newLen = Math.floor(input.length / ratio);
  const result = new Float32Array(newLen);
  for (let i = 0; i < newLen; i++) {
    const src = i * ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = src - i0;
    result[i] = (input[i0] ?? 0) * (1 - frac) + (input[i1] ?? 0) * frac;
  }
  return result;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function browserSpeak(text: string, languageCode: string): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !window.speechSynthesis || !text.trim()) {
      resolve();
      return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = languageCode === "kn-IN" ? "kn-IN" : "en-IN";
    u.rate = 1.05;
    const voices = window.speechSynthesis.getVoices();
    const prefer =
      voices.find((v) =>
        languageCode === "kn-IN"
          ? /kn|kannada/i.test(`${v.lang} ${v.name}`)
          : /en-IN|india/i.test(`${v.lang} ${v.name}`),
      ) ||
      voices.find((v) =>
        v.lang?.toLowerCase().startsWith(languageCode === "kn-IN" ? "kn" : "en"),
      );
    if (prefer) u.voice = prefer;
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

/**
 * Pre-Catalyst voice path: continuous WebSocket streaming call
 * (mic PCM → Sarvam VAD STT → answer TTS). This is the version that
 * worked smoothly before LiveKit.
 */
export default function VoiceCall({
  sessionId,
  languageCode,
  onTurn,
  onError,
  onCallActiveChange,
}: Props) {
  const [state, setState] = useState<VoiceState>("idle");
  const [caption, setCaption] = useState("");
  const [heard, setHeard] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const inCallRef = useRef(false);
  const mutedSendRef = useRef(false);
  const languageRef = useRef(languageCode);
  const onTurnRef = useRef(onTurn);
  const onErrorRef = useRef(onError);
  const onCallActiveChangeRef = useRef(onCallActiveChange);

  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const pendingTurnRef = useRef<ChatTurn | null>(null);
  const lastAnswerRef = useRef("");
  const speakingRef = useRef(false);

  useEffect(() => {
    languageRef.current = languageCode;
    onTurnRef.current = onTurn;
    onErrorRef.current = onError;
    onCallActiveChangeRef.current = onCallActiveChange;
  }, [languageCode, onCallActiveChange, onError, onTurn]);

  const stopMic = useCallback(() => {
    try {
      processorRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    processorRef.current = null;
    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;
    audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
  }, []);

  const endCall = useCallback(() => {
    const wasInCall = inCallRef.current;
    inCallRef.current = false;
    if (wasInCall) onCallActiveChangeRef.current?.(false);
    try {
      wsRef.current?.send(JSON.stringify({ type: "end" }));
    } catch {
      /* ignore */
    }
    wsRef.current?.close();
    wsRef.current = null;
    stopMic();
    if (audioElRef.current) {
      audioElRef.current.pause();
      audioElRef.current = null;
    }
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    setState("idle");
    setCaption("");
    setHeard("");
  }, [stopMic]);

  const endCallRef = useRef(endCall);
  endCallRef.current = endCall;
  useEffect(() => () => endCallRef.current(), []);

  const playAudioBase64 = useCallback(async (b64: string, contentType = "audio/mpeg") => {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const blob = new Blob([bytes], { type: contentType || "audio/mpeg" });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.volume = 0.78;
    audioElRef.current = audio;
    mutedSendRef.current = true;
    let ok = false;
    try {
      await audio.play();
      ok = true;
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
      });
    } catch {
      ok = false;
    } finally {
      URL.revokeObjectURL(url);
      mutedSendRef.current = false;
      audioElRef.current = null;
    }
    return ok;
  }, []);

  const startMicStreaming = useCallback(async (ws: WebSocket) => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: true,
      },
    });
    mediaStreamRef.current = stream;
    const audioCtx = new AudioContext({ sampleRate: 48000 });
    if (audioCtx.state === "suspended") await audioCtx.resume();
    audioCtxRef.current = audioCtx;
    const source = audioCtx.createMediaStreamSource(stream);
    const processor = audioCtx.createScriptProcessor(2048, 1, 1);
    processorRef.current = processor;

    processor.onaudioprocess = (ev) => {
      if (!inCallRef.current || ws.readyState !== WebSocket.OPEN) return;
      if (mutedSendRef.current) return;
      const input = ev.inputBuffer.getChannelData(0);
      const down = downsampleTo16k(input, audioCtx.sampleRate);
      const pcm = floatTo16BitPCM(down);
      const b64 = bytesToBase64(new Uint8Array(pcm.buffer));
      try {
        ws.send(JSON.stringify({ type: "audio_chunk", audio_base64: b64 }));
      } catch {
        /* ignore */
      }
    };

    const gain = audioCtx.createGain();
    gain.gain.value = 0;
    source.connect(processor);
    processor.connect(gain);
    gain.connect(audioCtx.destination);
  }, []);

  const startCall = useCallback(() => {
    if (inCallRef.current) return;
    try {
      window.speechSynthesis?.getVoices();
    } catch {
      /* ignore */
    }
    setState("connecting");
    setCaption("Connecting…");
    setHeard("");
    const ws = new WebSocket(voiceRealtimeWsUrl(sessionId, languageRef.current));
    wsRef.current = ws;
    inCallRef.current = true;
    onCallActiveChangeRef.current?.(true);

    ws.onopen = () => setCaption("Connected — starting mic…");
    ws.onerror = () => {
      setState("error");
      onErrorRef.current?.(
        "Voice connection failed — start the local API (port 8000) or check the backend URL.",
      );
      endCall();
    };
    ws.onclose = () => {
      if (inCallRef.current) endCall();
    };
    ws.onmessage = async (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as Record<string, unknown>;
        const type = msg.type as string;

        if (type === "ready") {
          setState("connecting");
          setCaption(
            languageRef.current === "kn-IN" ? "ಸಂಪರ್ಕ… ಶುಭಾಶಯ" : "Connected — greeting…",
          );
          try {
            await startMicStreaming(ws);
            mutedSendRef.current = true;
          } catch {
            setState("error");
            onErrorRef.current?.("Microphone access denied or unavailable.");
            endCall();
          }
        } else if (type === "greeting") {
          const text = String(msg.text ?? "");
          lastAnswerRef.current = text;
          setCaption(text);
          setState("speaking");
          mutedSendRef.current = true;
        } else if (type === "vad") {
          const signal = String(msg.signal ?? "");
          if (signal === "START_SPEECH") {
            if (speakingRef.current || mutedSendRef.current) return;
            setState("listening");
            setCaption("Listening… keep speaking");
            pendingTurnRef.current = pendingTurnRef.current || { question: "", answer: "" };
          } else if (signal === "END_SPEECH") {
            if (!speakingRef.current) {
              setCaption("Pause detected — continue or wait…");
            }
          }
        } else if (type === "caption") {
          if (!speakingRef.current && !mutedSendRef.current) {
            setCaption(String(msg.text ?? ""));
          }
        } else if (type === "status") {
          const s = String(msg.state ?? "");
          if (s === "thinking") {
            mutedSendRef.current = true;
            setState("thinking");
            setCaption(
              languageRef.current === "kn-IN" ? "ಯೋಚಿಸುತ್ತಿದೆ… mic mute" : "Thinking… mic muted",
            );
          } else if (s === "busy") {
            mutedSendRef.current = true;
            setState("busy");
            setCaption(
              String(msg.message ?? "") ||
                (languageRef.current === "kn-IN"
                  ? "ಉತ್ತರಿಸುತ್ತಿದೆ — ಸ್ವಲ್ಪ ನಿಲ್ಲಿಸಿ"
                  : "Still answering — pause a moment"),
            );
          } else if (s === "speaking") {
            mutedSendRef.current = true;
            setState("speaking");
            setCaption(lastAnswerRef.current || "Speaking…");
          } else if (s === "reconnecting") {
            setState("connecting");
            setCaption(
              languageRef.current === "kn-IN"
                ? "ಧ್ವನಿ ಗುರುತಿಸುವಿಕೆ ಮರುಸಂಪರ್ಕಗೊಳ್ಳುತ್ತಿದೆ…"
                : "Reconnecting speech recognition…",
            );
            mutedSendRef.current = true;
          } else if (s === "listening") {
            if (!speakingRef.current) {
              setState("listening");
              setCaption(
                languageRef.current === "kn-IN" ? "ಕೇಳುತ್ತಿದೆ — ಮಾತನಾಡಿ" : "Listening — speak now",
              );
              mutedSendRef.current = false;
            }
          }
        } else if (type === "transcript") {
          const text = String(msg.text ?? "");
          setHeard(text);
          if (msg.rejected) {
            setCaption(
              languageRef.current === "kn-IN"
                ? `ಕೇಳಿದೆ: "${text}" — clear ಆಗಿ ಹೇಳಿ`
                : `Heard: "${text}" — please speak clearly`,
            );
          } else if (msg.partial) {
            setCaption(languageRef.current === "kn-IN" ? "ಕೇಳುತ್ತಿದೆ…" : "Listening…");
          }
          pendingTurnRef.current = { question: text, answer: "" };
          if (!msg.partial) lastAnswerRef.current = "";
        } else if (type === "token") {
          lastAnswerRef.current += String(msg.text ?? "");
          setCaption(
            lastAnswerRef.current.slice(0, 180) + (lastAnswerRef.current.length > 180 ? "…" : ""),
          );
        } else if (type === "answer") {
          const answer = String(msg.text ?? lastAnswerRef.current);
          lastAnswerRef.current = answer;
          setCaption(answer);
          const rows = Array.isArray(msg.rows)
            ? (msg.rows as Record<string, unknown>[])
            : undefined;
          onTurnRef.current({
            question: pendingTurnRef.current?.question || "(voice)",
            answer,
            tool: (msg.tool as string) ?? null,
            query: (msg.query as string) ?? null,
            rows: rows?.length ? rows : undefined,
          });
          pendingTurnRef.current = null;
        } else if (type === "speak_text") {
          if (!speakingRef.current) {
            speakingRef.current = true;
            mutedSendRef.current = true;
            setState("speaking");
            const text = String(msg.text ?? lastAnswerRef.current);
            const lang = String(msg.language ?? languageRef.current);
            setCaption(text);
            await browserSpeak(text, lang);
            speakingRef.current = false;
            mutedSendRef.current = false;
            setState("listening");
            setCaption(
              languageRef.current === "kn-IN" ? "ಕೇಳುತ್ತಿದೆ — ಮಾತನಾಡಿ" : "Listening — speak now",
            );
          }
        } else if (type === "audio_chunk") {
          speakingRef.current = true;
          mutedSendRef.current = true;
          setState("speaking");
          setCaption(lastAnswerRef.current || "Speaking…");
          const ctype = String(msg.content_type ?? "audio/mpeg");
          const ok = await playAudioBase64(String(msg.audio_base64 ?? ""), ctype);
          if (!ok && lastAnswerRef.current) {
            await browserSpeak(lastAnswerRef.current, languageRef.current);
          }
          speakingRef.current = false;
          mutedSendRef.current = false;
          setState("listening");
          setCaption(
            languageRef.current === "kn-IN" ? "ಕೇಳುತ್ತಿದೆ — ಮಾತನಾಡಿ" : "Listening — speak now",
          );
        } else if (type === "audio_done") {
          if (!speakingRef.current) {
            setState("listening");
            setCaption(
              languageRef.current === "kn-IN" ? "ಕೇಳುತ್ತಿದೆ — ಮಾತನಾಡಿ" : "Listening — speak now",
            );
            mutedSendRef.current = false;
          }
        } else if (type === "error") {
          onErrorRef.current?.(String(msg.message ?? "Voice error"));
          setCaption(String(msg.message ?? "Voice error"));
        }
      } catch {
        /* ignore */
      }
    };
  }, [endCall, playAudioBase64, sessionId, startMicStreaming]);

  const active = state !== "idle" && state !== "error";

  if (!active) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-[var(--navy)]/20 bg-[var(--navy)] px-3 py-2 text-white">
        <span className="text-sm text-white/80" aria-hidden>
          ◉
        </span>
        <p className="min-w-0 flex-1 truncate text-xs text-white/80 sm:text-sm">
          {languageCode === "kn-IN"
            ? "ವಾಯ್ಸ್ — ಮಾತನಾಡಿ, ಉತ್ತರ ಕೇಳಿ"
            : "Voice — speak your question, hear the reply"}
        </p>
        <button
          type="button"
          onClick={startCall}
          className="shrink-0 rounded-md bg-[var(--saffron)] px-3 py-1.5 text-xs font-semibold text-white hover:opacity-95 sm:text-sm"
        >
          {languageCode === "kn-IN" ? "ಕರೆ ಪ್ರಾರಂಭ" : "Start call"}
        </button>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[radial-gradient(ellipse_at_center,#16325a_0%,#070f1c_70%)] text-white">
      <header className="flex items-center justify-between px-5 py-4">
        <div>
          <p className="font-display text-lg">Crime AI · Voice</p>
          <p className="text-xs uppercase tracking-widest text-white/60">{state}</p>
        </div>
        <button
          type="button"
          onClick={endCall}
          className="rounded-full bg-red-600 px-5 py-2.5 text-sm font-semibold"
        >
          {languageCode === "kn-IN" ? "ಕರೆ ಮುಗಿಸಿ" : "End call"}
        </button>
      </header>

      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-6">
        <div
          className={`h-[min(42vw,220px)] w-[min(42vw,220px)] rounded-full bg-[radial-gradient(circle_at_35%_30%,#ffe0a3,#e87722_45%,#0b1f3a_85%)] shadow-[0_0_60px_rgba(232,119,34,0.35)] ${
            state === "listening" ||
            state === "speaking" ||
            state === "thinking" ||
            state === "connecting" ||
            state === "busy"
              ? "animate-pulse"
              : ""
          } ${state === "speaking" ? "shadow-[0_0_80px_rgba(232,119,34,0.55)]" : ""}`}
          aria-hidden
        />
        {heard ? <p className="max-w-xl text-center text-sm text-white/70">You: {heard}</p> : null}
        <p className="max-w-2xl text-center font-display text-lg leading-snug text-white/95 sm:text-xl">
          {caption || "…"}
        </p>
        <p className="text-xs uppercase tracking-widest text-white/45">
          {state === "listening"
            ? "mic open"
            : state === "thinking" || state === "speaking" || state === "busy"
              ? "mic muted"
              : state}
        </p>
      </div>

      <footer className="px-5 py-6 text-center text-xs text-white/50">
        Speak the full question · short pauses are OK · wait ~1s after finishing
      </footer>
    </div>
  );
}
