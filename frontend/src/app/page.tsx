"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChatTurn,
  ChatSessionSummary,
  ChatSuggestion,
  exportInvestigationPdf,
  fetchChatHistory,
  fetchChatSessions,
  streamChatMessage,
  wakeApi,
} from "@/lib/api";
import { getRole, getUsername, getFullName, PDF_EXPORT_ROLES } from "@/lib/auth";
import AppShell from "@/components/AppShell";

const VoiceCall = dynamic(() => import("@/components/VoiceCall"), {
  ssr: false,
  loading: () => null,
});
const CaseNetworkPanel = dynamic(() => import("@/components/CaseNetworkPanel"), {
  ssr: false,
  loading: () => (
    <div className="rounded-md border border-[var(--border)] bg-white/60 p-3 text-xs text-[var(--muted)]">
      Loading network panel…
    </div>
  ),
});

const TURNS_KEY = (id: string) => `crimeai_turns_${id}`;
const LOCAL_SESSIONS_KEY = "crimeai_local_sessions";

type WelcomeAction = {
  id: string;
  emoji: string;
  labelEn: string;
  labelKn: string;
  kind: "send" | "dashboard" | "export";
  promptEn?: string;
  promptKn?: string;
};

const WELCOME_ACTIONS: WelcomeAction[] = [
  {
    id: "fir",
    emoji: "📋",
    labelEn: "Search FIR",
    labelKn: "FIR ಹುಡುಕಿ",
    kind: "send",
    promptEn: "Show FIR 104430006202600001",
    promptKn: "FIR 104430006202600001 ತೋರಿಸಿ",
  },
  {
    id: "person",
    emoji: "👤",
    labelEn: "Search Person",
    labelKn: "ವ್ಯಕ್ತಿ ಹುಡುಕಿ",
    kind: "send",
    promptEn: "Tell me about Yusuf Ali",
    promptKn: "ಯೂಸುಫ್ ಅಲಿ ಬಗ್ಗೆ ಹೇಳಿ",
  },
  {
    id: "analytics",
    emoji: "📈",
    labelEn: "Crime Analytics",
    labelKn: "ಅಪರಾಧ ವಿಶ್ಲೇಷಣೆ",
    kind: "send",
    promptEn: "Show crime trends and early warnings",
    promptKn: "ಅಪರಾಧ ಪ್ರವೃತ್ತಿ ಮತ್ತು ಆರಂಭಿಕ ಎಚ್ಚರಿಕೆಗಳನ್ನು ತೋರಿಸಿ",
  },
  {
    id: "network",
    emoji: "🕸",
    labelEn: "Criminal Network",
    labelKn: "ಅಪರಾಧ ಜಾಲ",
    kind: "send",
    promptEn: "Show criminal network for Yusuf Ali",
    promptKn: "ಯೂಸುಫ್ ಅಲಿ ಅಪರಾಧ ಜಾಲವನ್ನು ತೋರಿಸಿ",
  },
  {
    id: "dashboard",
    emoji: "🛡",
    labelEn: "Officer Dashboard",
    labelKn: "ಅಧಿಕಾರಿ ಡ್ಯಾಶ್‌ಬೋರ್ಡ್",
    kind: "dashboard",
  },
  {
    id: "report",
    emoji: "📄",
    labelEn: "Generate Investigation Report",
    labelKn: "ತನಿಖಾ ವರದಿ ರಚಿಸಿ",
    kind: "export",
    promptEn: "Export investigation report",
    promptKn: "ತನಿಖಾ ವರದಿ ರಫ್ತು ಮಾಡಿ",
  },
];

function sessionOwner() {
  return getUsername() || getFullName() || "investigator";
}

function getOrCreateSessionId() {
  if (typeof window === "undefined") return "demo-session";
  const key = "crimeai_session_id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const owner = sessionOwner();
  const id = `sess-${String(owner).toLowerCase().replace(/\s+/g, "-")}-${Date.now().toString(36)}`;
  localStorage.setItem(key, id);
  return id;
}

function normalizeSuggestions(raw: unknown): ChatSuggestion[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: ChatSuggestion[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      const t = item.trim();
      if (t) out.push({ label: t, prompt: t });
      continue;
    }
    if (item && typeof item === "object") {
      const o = item as Record<string, unknown>;
      const prompt = String(o.prompt ?? o.message ?? o.label ?? "").trim();
      const label = String(o.label ?? o.prompt ?? o.message ?? "").trim() || prompt;
      if (prompt) out.push({ label, prompt });
    }
  }
  return out.length ? out : undefined;
}

function suggestionChips(turn: ChatTurn): ChatSuggestion[] {
  return normalizeSuggestions(turn.suggestions) ?? [];
}

function readLocalTurns(id: string): ChatTurn[] {
  try {
    const raw = localStorage.getItem(TURNS_KEY(id));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatTurn[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLocalTurns(id: string, turns: ChatTurn[]) {
  try {
    localStorage.setItem(TURNS_KEY(id), JSON.stringify(turns));
  } catch {
    /* quota */
  }
}

function mergeTurns(remote: ChatTurn[], local: ChatTurn[]): ChatTurn[] {
  const longer = remote.length >= local.length ? remote : local;
  const shorter = remote.length >= local.length ? local : remote;
  return longer.map((t, i) => {
    const other = shorter[i];
    if (!other) return t;
    return {
      ...t,
      rows: t.rows?.length ? t.rows : other.rows,
      answer: t.answer || other.answer,
      suggestions: normalizeSuggestions(t.suggestions) ?? normalizeSuggestions(other.suggestions),
    };
  });
}

function readLocalSessions(): ChatSessionSummary[] {
  try {
    const raw = localStorage.getItem(LOCAL_SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatSessionSummary[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function upsertLocalSession(id: string, turns: ChatTurn[]) {
  if (!id || id === "demo-session" || !turns.length) return;
  const title = turns[0]?.question?.slice(0, 80) || "Untitled chat";
  const next: ChatSessionSummary = {
    session_id: id,
    title,
    turn_count: turns.length,
    updated_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
  };
  const prev = readLocalSessions().filter((s) => s.session_id !== id);
  const merged = [next, ...prev].slice(0, 40);
  try {
    localStorage.setItem(LOCAL_SESSIONS_KEY, JSON.stringify(merged));
  } catch {
    /* quota */
  }
}

function mergeSessionLists(remote: ChatSessionSummary[], local: ChatSessionSummary[]) {
  const byId = new Map<string, ChatSessionSummary>();
  for (const s of [...local, ...remote]) {
    const existing = byId.get(s.session_id);
    if (!existing || (s.updated_at || "") > (existing.updated_at || "")) {
      byId.set(s.session_id, s);
    }
  }
  return [...byId.values()].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}

function ChatPage() {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState("demo-session");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [botLang, setBotLang] = useState<"en-IN" | "kn-IN">("en-IN");
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [voiceCallActive, setVoiceCallActive] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [exporting, setExporting] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const kn = botLang === "kn-IN";
  const canExportPdf = PDF_EXPORT_ROLES.includes(getRole() || "");
  const displayName = getFullName() || "Inspector";

  const refreshSessions = useCallback(async () => {
    const local = readLocalSessions();
    try {
      const data = await fetchChatSessions();
      setSessions(mergeSessionLists(data.sessions || [], local));
    } catch {
      setSessions(local);
    }
  }, []);

  const loadSession = useCallback(async (id: string) => {
    setSessionId(id);
    localStorage.setItem("crimeai_session_id", id);
    setError(null);
    const local = readLocalTurns(id);
    setTurns(local);
    setHistoryLoaded(true);
    try {
      const data = await fetchChatHistory(id);
      const remote = data.turns || [];
      const merged = mergeTurns(remote, local);
      setTurns(merged);
      writeLocalTurns(id, merged);
      upsertLocalSession(id, merged);
    } catch {
      /* keep local turns */
    }
  }, []);

  useEffect(() => {
    void wakeApi();
    const id = getOrCreateSessionId();
    const saved = localStorage.getItem("crimeai_language");
    if (saved === "kn-IN" || saved === "en-IN") setBotLang(saved);
    void loadSession(id);
    void refreshSessions();
  }, [loadSession, refreshSessions]);

  // Dashboard "Ask about this" chips land here with ?q= or sessionStorage.
  useEffect(() => {
    if (!historyLoaded || streaming || voiceCallActive) return;
    let pending = "";
    try {
      const params = new URLSearchParams(window.location.search);
      pending = (params.get("q") || sessionStorage.getItem("crimeai_pending_prompt") || "").trim();
      if (params.get("q")) {
        window.history.replaceState({}, "", (process.env.NEXT_PUBLIC_BASE_PATH || "") + "/" || "/");
      }
      sessionStorage.removeItem("crimeai_pending_prompt");
    } catch {
      pending = "";
    }
    if (!pending) return;
    void handleSend(pending);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once after history loads
  }, [historyLoaded]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, streaming]);

  useEffect(() => {
    if (sessionId && sessionId !== "demo-session") {
      writeLocalTurns(sessionId, turns);
      upsertLocalSession(sessionId, turns);
    }
  }, [sessionId, turns]);

  function setLanguage(code: "en-IN" | "kn-IN") {
    setBotLang(code);
    localStorage.setItem("crimeai_language", code);
  }

  async function handleSend(override?: string) {
    const question = (override ?? input).trim();
    if (!question || streaming) return;
    if (!override) setInput("");
    setError(null);
    setStreaming(true);

    let turnIndex = 0;
    setTurns((prev) => {
      turnIndex = prev.length;
      return [...prev, { question, answer: "" }];
    });

    try {
      await streamChatMessage(
        sessionId,
        question,
        (event) => {
          setTurns((prev) => {
            const next = [...prev];
            const turn = { ...next[turnIndex] };
            if (!turn) return prev;
            if ("token" in event && typeof event.token === "string") {
              turn.answer = (turn.answer || "") + event.token;
            } else if ("done" in event) {
              turn.tool = event.tool;
              turn.query = event.query;
              if (event.answer && !turn.answer) turn.answer = event.answer;
              if (Array.isArray(event.rows) && event.rows.length) {
                turn.rows = event.rows as Record<string, unknown>[];
              }
              const chips = normalizeSuggestions(event.suggestions);
              if (chips) turn.suggestions = chips;
              // "Reply in Kannada" should stick for the rest of the session.
              const replyLang = event.language_code;
              if (replyLang === "en-IN" || replyLang === "kn-IN") {
                setLanguage(replyLang);
              }
            }
            next[turnIndex] = turn;
            return next;
          });
        },
        botLang,
      );
      void refreshSessions();
    } catch {
      setError(
        kn
          ? "ಬ್ಯಾಕೆಂಡ್ ತಲುಪಲಾಗಲಿಲ್ಲ. ಸರ್ವರ್ ಚಾಲನೆಯಲ್ಲಿದೆಯೇ?"
          : "Could not reach the backend. Is the API server running?",
      );
    } finally {
      setStreaming(false);
    }
  }

  async function handleExport() {
    if (!canExportPdf || turns.length === 0 || exporting) return;
    setExporting(true);
    setError(null);
    try {
      const blob = await exportInvestigationPdf(sessionId, turns);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `investigation_${sessionId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError(
        kn
          ? "PDF ರಫ್ತು ವಿಫಲವಾಯಿತು. ನಿಮ್ಮ ಪಾತ್ರಕ್ಕೆ ಅನುಮತಿ ಇದೆಯೇ?"
          : "PDF export failed. Check your role permissions and backend.",
      );
    } finally {
      setExporting(false);
    }
  }

  function handleWelcomeAction(action: WelcomeAction) {
    if (action.kind === "dashboard") {
      window.location.href = `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/dashboard/`;
      return;
    }
    if (action.kind === "export") {
      if (turns.length > 0 && canExportPdf) {
        void handleExport();
        return;
      }
      const prompt = kn ? action.promptKn : action.promptEn;
      if (prompt) void handleSend(prompt);
      return;
    }
    const prompt = kn ? action.promptKn : action.promptEn;
    if (prompt) void handleSend(prompt);
  }

  function handleNewChat() {
    const owner = sessionOwner();
    const id = `sess-${String(owner).toLowerCase().replace(/\s+/g, "-")}-${Date.now().toString(36)}`;
    localStorage.setItem("crimeai_session_id", id);
    setSessionId(id);
    setTurns([]);
    setInput("");
    setError(null);
    setHistoryLoaded(true);
    setHistoryOpen(true);
  }

  return (
    <main className="flex h-full gap-0">
      <aside
        className={`shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] ${
          historyOpen ? "flex w-52 lg:w-56" : "hidden"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--border)] px-2.5 py-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            {kn ? "ಇತಿಹಾಸ" : "History"}
          </p>
          <button
            type="button"
            onClick={() => setHistoryOpen(false)}
            className="text-xs text-[var(--muted)] hover:text-[var(--navy)]"
          >
            ✕
          </button>
        </div>
        <div className="p-2">
          <button
            type="button"
            onClick={handleNewChat}
            className="w-full rounded-md bg-[var(--saffron)] px-2.5 py-1.5 text-xs font-medium text-white hover:opacity-90"
          >
            {kn ? "+ ಹೊಸ ಚಾಟ್" : "+ New chat"}
          </button>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-1.5 pb-2">
          {sessions.length === 0 && (
            <p className="px-2 py-2 text-xs text-[var(--muted)]">
              {kn ? "ಇನ್ನೂ ಉಳಿಸಿದ ಚಾಟ್‌ಗಳಿಲ್ಲ." : "No saved chats yet."}
            </p>
          )}
          {sessions.map((s) => {
            const active = s.session_id === sessionId;
            return (
              <button
                key={s.session_id}
                type="button"
                onClick={() => void loadSession(s.session_id)}
                className={`w-full rounded-md px-2 py-1.5 text-left text-xs transition ${
                  active
                    ? "bg-[var(--navy)] text-white"
                    : "text-[var(--navy)] hover:bg-[var(--sand)]"
                }`}
              >
                <span className="line-clamp-2 font-medium leading-snug">{s.title}</span>
                <span className={`mt-0.5 block ${active ? "text-white/70" : "text-[var(--muted)]"}`}>
                  {s.turn_count} {kn ? "ಸಂದೇಶ" : "turns"}
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col px-3 py-2 md:px-4 md:py-3">
        <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] pb-2">
          {!historyOpen && (
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs text-[var(--navy)]"
            >
              {kn ? "ಇತಿಹಾಸ" : "History"}
            </button>
          )}
          <h1 className="font-display text-lg text-[var(--navy)] md:text-xl">
            {kn ? "ತನಿಖಾ ಸಹಾಯಕ" : "Investigator assistant"}
          </h1>
          <span className="hidden text-xs text-[var(--muted)] sm:inline">
            · {displayName}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <div className="flex overflow-hidden rounded-md border border-[var(--border)] text-xs font-medium">
              <button
                type="button"
                onClick={() => setLanguage("en-IN")}
                className={`px-2 py-1 ${
                  botLang === "en-IN"
                    ? "bg-[var(--navy)] text-white"
                    : "bg-[var(--surface)] text-[var(--muted)]"
                }`}
              >
                EN
              </button>
              <button
                type="button"
                onClick={() => setLanguage("kn-IN")}
                className={`px-2 py-1 ${
                  botLang === "kn-IN"
                    ? "bg-[var(--navy)] text-white"
                    : "bg-[var(--surface)] text-[var(--muted)]"
                }`}
              >
                ಕನ್ನಡ
              </button>
            </div>
            <button
              type="button"
              onClick={handleNewChat}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs text-[var(--navy)]"
            >
              {kn ? "ಹೊಸ" : "New"}
            </button>
            {canExportPdf && (
              <button
                type="button"
                onClick={() => void handleExport()}
                disabled={turns.length === 0 || exporting}
                className="rounded-md bg-[var(--navy)] px-2.5 py-1 text-xs text-white disabled:opacity-40"
              >
                {exporting ? (kn ? "…" : "…") : kn ? "PDF" : "PDF"}
              </button>
            )}
          </div>
        </header>

        <div className="mt-2">
          <VoiceCall
            sessionId={sessionId}
            languageCode={botLang}
            onTurn={(turn) => {
              setTurns((prev) => [
                ...prev,
                {
                  ...turn,
                  suggestions: normalizeSuggestions(turn.suggestions),
                },
              ]);
              void refreshSessions();
            }}
            onError={setError}
            onCallActiveChange={(active) => {
              setVoiceCallActive(active);
              // Reload DB history after a call so PDF export has full rows.
              if (!active && sessionId) {
                void loadSession(sessionId);
                void refreshSessions();
              }
            }}
          />
        </div>

        {!voiceCallActive && (
          <div className="mt-2 min-h-0 flex-1 space-y-2.5 overflow-y-auto py-1">
            {!historyLoaded && (
              <p className="text-sm text-[var(--muted)]">
                {kn ? "ಇತಿಹಾಸ ಲೋಡ್ ಆಗುತ್ತಿದೆ…" : "Loading history…"}
              </p>
            )}

            {historyLoaded && turns.length === 0 && (
              <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-3 md:px-4">
                <p className="text-sm text-[var(--navy)]">
                  {kn ? `ನಮಸ್ಕಾರ, ${displayName}.` : `Hello, ${displayName}.`}{" "}
                  <span className="text-[var(--muted)]">
                    {kn
                      ? "FIR, ವ್ಯಕ್ತಿ, ಟ್ರೆಂಡ್ ಅಥವಾ ಜಾಲ ಕೇಳಿ."
                      : "Ask about an FIR, person, trend, or network."}
                  </span>
                </p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {WELCOME_ACTIONS.map((action) => (
                    <button
                      key={action.id}
                      type="button"
                      onClick={() => handleWelcomeAction(action)}
                      disabled={streaming}
                      className="rounded-md border border-[var(--border)] bg-[var(--sand)] px-2.5 py-1.5 text-left text-xs text-[var(--navy)] transition hover:border-[var(--saffron)] hover:bg-[var(--surface)] disabled:opacity-40"
                    >
                      <span className="mr-1" aria-hidden>
                        {action.emoji}
                      </span>
                      {kn ? action.labelKn : action.labelEn}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((turn, i) => {
              const chips = suggestionChips(turn);
              const isLast = i === turns.length - 1;
              return (
                <div key={`${sessionId}-${i}`} className="space-y-1.5">
                  <div className="ml-auto w-fit max-w-[min(92%,48rem)] rounded-2xl rounded-br-md bg-[var(--navy)] px-3 py-1.5 text-sm text-white">
                    {turn.question}
                  </div>
                  <div className="w-fit max-w-[min(92%,48rem)] whitespace-pre-wrap rounded-2xl rounded-bl-md border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm leading-relaxed text-[var(--navy)]">
                    {turn.answer ||
                      (streaming && isLast ? (
                        <span className="text-[var(--muted)]">
                          {kn ? "ಯೋಚಿಸುತ್ತಿದೆ…" : "Thinking…"}
                        </span>
                      ) : null)}
                    {turn.rows && turn.rows.length > 0 && (
                      <CaseNetworkPanel rows={turn.rows} languageCode={botLang} />
                    )}
                    {(turn.tool || turn.query) && (
                      <details className="mt-1.5 border-t border-[var(--border)] pt-1.5 text-xs text-[var(--muted)]">
                        <summary className="cursor-pointer select-none font-medium text-[var(--navy)]/70 hover:text-[var(--navy)]">
                          {kn ? "ಆಡಿಟ್" : "Audit"}
                        </summary>
                        <div className="mt-1 space-y-1">
                          {turn.tool && (
                            <p>
                              <span className="font-medium">Tool:</span> {turn.tool}
                            </p>
                          )}
                          {turn.query && (
                            <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-[var(--sand)] p-2 text-[10px] text-[var(--navy)]">
                              {turn.query}
                            </pre>
                          )}
                        </div>
                      </details>
                    )}
                  </div>
                  {chips.length > 0 && !streaming && (
                    <div className="flex flex-wrap gap-1.5 pl-0.5">
                      {chips.map((chip) => (
                        <button
                          key={`${chip.label}-${chip.prompt}`}
                          type="button"
                          onClick={() => void handleSend(chip.prompt)}
                          className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-0.5 text-xs text-[var(--navy)] hover:border-[var(--saffron)] hover:text-[var(--saffron)]"
                        >
                          {chip.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            {error && <p className="text-sm text-red-600">{error}</p>}
            <div ref={bottomRef} />
          </div>
        )}

        {!voiceCallActive && (
          <div className="flex gap-2 border-t border-[var(--border)] pt-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder={kn ? "ನಿಮ್ಮ ಪ್ರಶ್ನೆ ಟೈಪ್ ಮಾಡಿ…" : "Type your question…"}
              disabled={streaming}
              className="flex-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--navy)] placeholder:text-[var(--muted)] disabled:opacity-60"
            />
            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={streaming || !input.trim()}
              className="rounded-md bg-[var(--saffron)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              {kn ? "ಕಳುಹಿಸಿ" : "Send"}
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

export default function HomePage() {
  return (
    <AppShell>
      <ChatPage />
    </AppShell>
  );
}
