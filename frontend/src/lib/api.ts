import { clearSession, getToken } from "@/lib/auth";
import { appPath } from "@/lib/paths";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export async function authFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type") && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (res.status === 401 && typeof window !== "undefined") {
    clearSession();
    window.location.href = appPath("/login/");
  }
  return res;
}

export async function login(username: string, password: string) {
  const res = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`Login failed: ${res.status}`);
  return res.json() as Promise<{
    access_token: string;
    role: string;
    full_name: string;
    username: string;
  }>;
}

export type ChatSuggestion = { label: string; prompt: string };

export type ChatTurn = {
  question: string;
  answer: string;
  tool?: string | null;
  query?: string | null;
  rows?: Record<string, unknown>[];
  /** Follow-up chips from the backend (or string prompts). */
  suggestions?: ChatSuggestion[] | string[];
};

export type ChatSessionSummary = {
  session_id: string;
  title: string;
  turn_count: number;
  updated_at: string;
  started_at: string;
};

export async function fetchChatSessions() {
  const res = await authFetch("/chat/sessions");
  if (!res.ok) throw new Error(`Sessions request failed: ${res.status}`);
  return res.json() as Promise<{ sessions: ChatSessionSummary[] }>;
}

export async function fetchChatHistory(sessionId: string) {
  const res = await authFetch(`/chat/history?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error(`History request failed: ${res.status}`);
  return res.json() as Promise<{ session_id: string; turns: ChatTurn[] }>;
}

export function getApiBaseUrl() {
  return API_BASE_URL;
}

export function voiceRealtimeWsUrl(sessionId: string, languageCode: string) {
  const token = getToken() ?? "";
  const base = API_BASE_URL.replace(/^http/, "ws");
  return `${base}/voice/realtime?session_id=${encodeURIComponent(sessionId)}&language_code=${encodeURIComponent(languageCode)}&token=${encodeURIComponent(token)}`;
}

export async function fetchVoiceStatus() {
  const res = await authFetch("/voice/realtime/status");
  if (!res.ok) throw new Error(`Voice status failed: ${res.status}`);
  return res.json() as Promise<{
    realtime: boolean;
    vad: boolean;
    streaming: boolean;
    transport: "livekit" | "websocket";
    livekit_ready: boolean;
  }>;
}

export async function fetchLiveKitToken(sessionId: string, identity: string) {
  const res = await authFetch("/voice/token", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, identity }),
  });
  if (!res.ok) throw new Error(`LiveKit token failed: ${res.status}`);
  return res.json() as Promise<{
    token: string | null;
    url: string | null;
    error?: string;
  }>;
}

export async function sendChatMessage(
  sessionId: string,
  message: string,
  languageCode: "en-IN" | "kn-IN" = "en-IN",
) {
  const res = await authFetch("/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message, language_code: languageCode }),
  });
  if (!res.ok) throw new Error(`Chat request failed: ${res.status}`);
  return res.json() as Promise<{
    answer: string;
    tool: string | null;
    query: string | null;
    rows: unknown[];
    suggestions?: unknown[];
    language_code?: string;
  }>;
}

export type ChatStreamEvent =
  | { token: string }
  | {
      done: true;
      answer?: string;
      tool: string | null;
      query: string | null;
      rows: unknown[];
      suggestions?: ChatSuggestion[] | string[] | { label: string; message: string }[];
      language_code?: string;
    };

export async function streamChatMessage(
  sessionId: string,
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  languageCode: "en-IN" | "kn-IN" = "en-IN",
) {
  const res = await authFetch("/chat/stream", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      message,
      language_code: languageCode,
    }),
  });
  if (!res.ok) throw new Error(`Stream request failed: ${res.status}`);
  if (!res.body) throw new Error("Stream response had no body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .map((l) => l.trim())
        .find((l) => l.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw || raw === "[DONE]") continue;
      try {
        onEvent(JSON.parse(raw) as ChatStreamEvent);
      } catch {
        /* skip malformed SSE chunk */
      }
    }
  }
}

export async function fetchDashboardSummary() {
  const res = await authFetch("/dashboard/summary");
  if (!res.ok) throw new Error(`Summary request failed: ${res.status}`);
  return res.json() as Promise<Record<string, number>>;
}

export async function fetchDashboardTrend() {
  const res = await authFetch("/dashboard/trend");
  if (!res.ok) throw new Error(`Trend request failed: ${res.status}`);
  return res.json() as Promise<{ month: string; crime_group_name: string; count: number }[]>;
}

export type Hotspot = {
  lat_bucket: number;
  lng_bucket: number;
  case_count: number;
  unit_name?: string;
  district_name?: string;
};

export async function fetchDashboardHotspots(minCases = 1) {
  const res = await authFetch(`/dashboard/hotspots?min_cases=${minCases}`);
  if (!res.ok) throw new Error(`Hotspots request failed: ${res.status}`);
  return res.json() as Promise<Hotspot[]>;
}

export async function fetchDashboardSocio() {
  const res = await authFetch("/dashboard/socio");
  if (!res.ok) throw new Error(`Socio request failed: ${res.status}`);
  return res.json() as Promise<{
    accused_age_bands: { age_band: string; count: number }[];
    accused_gender: { gender: string; count: number }[];
    victim_age_bands: { age_band: string; count: number }[];
    victim_gender: { gender: string; count: number }[];
    complainant_occupations: { occupation_name: string; count: number }[];
  }>;
}

export async function fetchEarlyWarnings(limit = 10) {
  const res = await authFetch(`/dashboard/early-warnings?limit=${limit}`);
  if (!res.ok) throw new Error(`Early warnings request failed: ${res.status}`);
  return res.json() as Promise<{
    warnings: {
      unit_name: string;
      district_name: string;
      crime_head_name: string;
      current_count: number;
      previous_count: number;
      delta: number;
      recommendation: string;
    }[];
  }>;
}

export async function fetchCrimePatterns(limit = 15) {
  const res = await authFetch(`/dashboard/patterns?limit=${limit}`);
  if (!res.ok) throw new Error(`Patterns request failed: ${res.status}`);
  return res.json() as Promise<{
    patterns: {
      unit_name: string;
      district_name: string;
      crime_head_name: string;
      crime_group_name: string;
      case_count: number;
      pattern: string;
      first_seen: string;
      last_seen: string;
    }[];
  }>;
}

export type NetworkNode = {
  id: string;
  label: string;
  type: string;
  props: Record<string, unknown>;
};

export type NetworkLink = {
  source: string;
  target: string;
  type: string;
};

export type NetworkGraph = {
  nodes: NetworkNode[];
  links: NetworkLink[];
  error?: string;
  source?: string;
};

export async function fetchNetworkGraph(
  opts?: string | { name?: string; crime_no?: string; crime_nos?: string[] },
) {
  const params = new URLSearchParams();
  if (typeof opts === "string") {
    if (opts.trim()) params.set("name", opts.trim());
  } else if (opts) {
    if (opts.name?.trim()) params.set("name", opts.name.trim());
    if (opts.crime_no?.trim()) params.set("crime_no", opts.crime_no.trim());
    if (opts.crime_nos?.length) {
      params.set("crime_nos", opts.crime_nos.map((c) => c.trim()).filter(Boolean).join(","));
    }
  }
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await authFetch(`/graph/network${qs}`);
  if (!res.ok) throw new Error(`Network request failed: ${res.status}`);
  return res.json() as Promise<NetworkGraph>;
}

export async function fetchNetworkSuggestions(limit = 12) {
  const res = await authFetch(`/graph/suggestions?limit=${limit}`);
  if (!res.ok) throw new Error(`Suggestions request failed: ${res.status}`);
  return res.json() as Promise<{ names: string[]; error?: string }>;
}

export async function exportInvestigationPdf(sessionId: string, turns: ChatTurn[]) {
  // Prefer server-side session history (includes rows_json from voice + chat).
  let payloadTurns = turns;
  try {
    const history = await fetchChatHistory(sessionId);
    if (Array.isArray(history.turns) && history.turns.length > 0) {
      payloadTurns = history.turns as ChatTurn[];
    }
  } catch {
    /* fall back to in-memory turns */
  }
  const res = await authFetch("/reports/pdf", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      turns: payloadTurns.map((t) => ({
        question: t.question,
        answer: t.answer,
        tool: t.tool ?? null,
        query: t.query ?? null,
        rows: t.rows ?? [],
      })),
    }),
  });
  if (!res.ok) throw new Error(`PDF export failed: ${res.status}`);
  return res.blob();
}

export type VoiceAskResult = {
  transcript: string;
  answer: string;
  tool?: string | null;
  query?: string | null;
  language?: string;
  content_type?: string;
  audio_base64: string;
};

/** Legacy one-shot mic upload (debug). Prefer voiceRealtimeWsUrl + /voice/realtime. */
export async function askVoice(
  sessionId: string,
  file: Blob,
  languageCode: "en-IN" | "kn-IN" = "en-IN",
) {
  const form = new FormData();
  const ext = file.type.includes("wav") ? "wav" : file.type.includes("webm") ? "webm" : "wav";
  form.append("file", file, `recording.${ext}`);
  const res = await authFetch(
    `/voice/ask?session_id=${encodeURIComponent(sessionId)}&language_code=${encodeURIComponent(languageCode)}`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error(`Voice ask failed: ${res.status}`);
  return res.json() as Promise<VoiceAskResult>;
}
