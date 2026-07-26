# Crime AI — End-to-End Plan (KSP Hackathon)

Updated stack decisions vs the original plan.md:
- **LLM**: Gemini 2.5 Flash (via `langchain-google-genai`, plugged into LangGraph) — replaces GPT-5/4.1
- **STT + TTS**: Sarvam AI for both (saarika ASR, bulbul TTS) — English + Kannada
- Everything else (LiveKit, Neon Postgres, Neo4j, FastAPI, Next.js, Zoho Catalyst) kept as in the original plan — full architecture, built in parallel across the 4-day sprint.

## 1. Architecture

```
                    Investigator
            🎤 Voice        💬 Chat
                  │
               LiveKit  (room + audio transport)
                  │
            Sarvam ASR (saarika-v2, en-IN / kn-IN)
                  │
      FastAPI + LangGraph Agent  ── Gemini 2.5 Flash (planning/routing/NL→SQL/answers)
                  │
    ┌─────────────┼──────────────┐
    │             │              │
 SQL Tool     Analytics      Graph Tool
    │             │              │
 Neon DB      Pandas/DuckDB   Neo4j
(PostgreSQL,                   │
 pgvector)        │              │
    └─────────────┼──────────────┘
                  │
          Evidence / Report Generator
                  │
            Sarvam TTS (bulbul-v2)
                  │
               LiveKit → investigator audio
```

## 2. Technology Stack

| Component | Technology |
|---|---|
| Frontend | Next.js 14 (App Router) + Tailwind |
| Backend | FastAPI (Python 3.11+) |
| Voice transport | LiveKit (agents framework) |
| ASR (STT) | Sarvam (`saarika:v2`, en-IN + kn-IN) |
| TTS | Sarvam (`bulbul:v2`) |
| LLM / Orchestration | Gemini 2.5 Flash + LangGraph |
| Primary DB | Neon PostgreSQL |
| Relationship graph | Neo4j (Aura) |
| Analytics | Pandas + DuckDB |
| Vector search | pgvector (inside Neon) — for semantic search over BriefFacts / MO text |
| Dashboard charts | Apache ECharts |
| Maps | Leaflet |
| Auth / File store | Zoho Catalyst (roles: Investigator, SHO, DSP, Analyst, Administrator) |
| Hosting | Zoho Catalyst or Railway/Render |

### Why Gemini 2.5 Flash
- Fast + cheap enough for interactive chat latency budgets that already include an STT and TTS round trip.
- Strong function/tool-calling support, which LangGraph relies on for the SQL/Graph/Analytics tool nodes.
- Long context window helps with schema-grounding (we pass the full KSP schema + few-shot NL→SQL examples in the system prompt).

### Why Sarvam for both STT and TTS
- Single vendor, single API key, consistent Kannada + English quality tuned for Indian languages/accents — reduces integration surface for a 4-day sprint.
- `saarika` STT and `bulbul` TTS both support `kn-IN` and `en-IN`, matching the "English + Kannada" requirement directly.

## 3. Database Schema (from supplied ER diagram)

Core entities already extracted from `Police_FIR_ER_Diagram (1).pdf`:
`CaseMaster, ComplainantDetails, ActSectionAssociation, Victim, Accused, ArrestSurrender, Act, Section, CrimeHeadActSection, CrimeHead, CrimeSubHead, CasteMaster, ReligionMaster, OccupationMaster, CaseStatusMaster, Court, District, State, Unit, UnitType, Rank, Designation, Employee, CaseCategory, GravityOffence, ChargesheetDetails`.

Full DDL lives in [`database/schema.sql`](../database/schema.sql), lookup/demo data in [`database/seed.sql`](../database/seed.sql).

Kept exactly as relational — Neon Postgres is a 1:1 fit since the ER diagram is already normalized. Neo4j is populated *from* Postgres (not a second source of truth) for the network-analysis queries where graph traversal beats recursive SQL (co-accused chains, shared-victim links across cases, officer caseload networks).

## 4. LangGraph Agents

1. **Conversation Agent** — memory, context, routing (Gemini 2.5 Flash as the router/planner)
2. **SQL Agent** — NL → parameterized SQL against Neon, schema-grounded, read-only role
3. **Analytics Agent** — trends, hotspots, charts (Pandas/DuckDB over Neon data)
4. **Relationship (Graph) Agent** — Cypher queries against Neo4j: criminal networks, shared accused, common-FIR links
5. **Report Agent** — investigation summary, timeline, PDF export

Every agent response includes: the generated SQL/Cypher, the tool used, and a plain-language explanation — this is the "Explainable AI with audit trails" requirement, and doubles as the PDF export content.

## 5. Voice Pipeline (Sarvam + LiveKit) — implemented in `backend/voice_agent/`

1. Investigator joins a LiveKit room (frontend gets a LiveKit token from `/voice/token`).
2. Mic audio streams into the LiveKit Agents worker (`voice_agent/worker.py`).
3. A local Silero VAD (bundled with `livekit-agents`, no extra credentials) segments speech into utterances.
4. Each utterance → **`SarvamSTT`** (`voice_agent/sarvam_stt.py`, batch `saarika:v2.5` REST call) → transcript.
5. Transcript → **`LangGraphLLM`** (`voice_agent/langgraph_llm.py`), a `livekit.agents.llm.LLM` wrapper around the *same* `agents/conversation_agent.py` graph used by `/chat` — identical SQL/Graph/Analytics tools, memory, and audit trail, just fed by voice instead of text.
6. Answer text → **`SarvamTTS`** (`voice_agent/sarvam_tts.py`, raw `linear16` PCM so LiveKit's `AudioEmitter` needs no decode step) → audio → published back into the room.
7. Text chat (`/chat`, `/chat/stream`) bypasses this whole pipeline (goes direct to LangGraph), so it works independently of voice.

There is no official LiveKit-Sarvam plugin, so `SarvamSTT`/`SarvamTTS` are custom classes implementing LiveKit's plugin interfaces directly against Sarvam's REST API (`core/sarvam_client.py`) — both were verified against the real API (including the streaming websocket variants, which exist but weren't needed: Sarvam's STT response doesn't expose partial transcripts anyway, so batch-per-utterance is equally responsive). `SarvamSTT.recognize()`, `SarvamTTS.synthesize()`, and `LangGraphLLM.chat()` were each exercised standalone with real API calls and produced correct output. **The one thing not yet tested is the worker actually joining a live LiveKit room** — `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` weren't available at build time.

## 6. Folder Structure (created)

```
crime-ai/
  backend/
    main.py
    requirements.txt
    .env.example
    routers/        (chat, voice, dashboard, graph, reports)
    agents/         (conversation, sql, analytics, graph, report)
    core/           (llm.py - Gemini client, sarvam_client.py, db.py, neo4j_client.py, config.py)
    voice_agent/    (LiveKit Agents worker: sarvam_stt.py, sarvam_tts.py, langgraph_llm.py, worker.py)
  frontend/         (Next.js + Tailwind — chat, dashboard, graph, map views)
  database/
    schema.sql
    seed.sql
  neo4j/
    schema.cypher
    load_from_postgres.py
  docs/
    END_TO_END_PLAN.md  (this file)
  presentation/
```

## 7. 4-Day Sprint

**Day 1 — Database & Backend**
- Import KSP schema into Neon (`database/schema.sql`)
- FastAPI CRUD + read endpoints over CaseMaster/Accused/Victim/etc.
- LiveKit room/token endpoint wired up
- Sarvam STT/TTS smoke-tested standalone (no LangGraph yet)

**Day 2 — AI Layer**
- LangGraph graph: Conversation → SQL Agent (Gemini 2.5 Flash, tool-calling)
- Conversation memory (per-session, LangGraph checkpointer)
- English/Kannada NL understanding verified against sample investigator queries
- Explainable responses (SQL + explanation returned alongside answer)

**Day 3 — Visualization**
- Dashboard (ECharts): Total FIR / Pending / Closed / Chargesheeted, crime trends by district→month→crime head
- Neo4j populated from Postgres, Relationship Agent + graph visualization in frontend
- Crime map (Leaflet) using `CaseMaster.latitude/longitude`
- Analytics agent (hotspots, trend detection)

**Day 4 — Final Integration**
- PDF export of conversation/investigation summary
- Zoho Catalyst Authentication (roles) + File Store (PDFs, reports, evidence)
- UI polish, demo script, presentation deck

## 8. Demo Flow (unchanged from plan.md)
🎤 "Show robbery cases in Hubballi." → FIR list
🎤 "Show only pending ones." → context retained, filtered
🎤 "Who is the investigating officer?" → Employee lookup
🎤 "Show accused history." → cross-case search
🎤 "Show criminal network." → Neo4j visualization
🎤 "Export this investigation." → PDF saved to Catalyst File Store

## 9. Env vars needed (see `backend/.env.example`)
`DATABASE_URL` (Neon), `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` (Aura), `GEMINI_API_KEY`, `SARVAM_API_KEY`, `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`, and Catalyst project credentials (added Day 4).
