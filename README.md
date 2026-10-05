# JanSahay — Multilingual Cooperative Governance & Legal Assistance Chatbot

Evidence-grounded, multilingual (English + Hindi + Gujarati + Marathi + Bengali + Tamil + Telugu + Kannada + Punjabi + Odia + Malayalam)
citizen-assistance PWA for cooperative governance, legal guidance, schemes, PMFBY,
financial literacy, and grievance redressal.

> **Core principle:** the LLM is **never** the source of truth. Every factual
> answer must be grounded in retrieved official documents with verifiable
> citations. If retrieval confidence is low or no supporting chunk exists, the
> system **abstains** — it never guesses.

---

## What It Does

- Answers questions from official government sources with verifiable citations
- **11 languages**: English, Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Punjabi, Odia, Malayalam
- Routes queries across 7 AnchorStore domains: agriculture, financial_inclusion, grievance, pacs_computerization, pacs_governance, pmfby, schemes (+ `out_of_scope`)
- Applies jurisdiction filtering (central + selected state — currently Gujarat)
- **Hybrid retrieval**: dense vector search (pgvector) + lexical search (RRF fusion)
- Abstains when evidence is insufficient — never guesses
- Prototype grievance workflow with follow-up questions, status lookup, and
  full multilingual localization (system text translated; user values preserved)
  (`is_official_submission: false` — no real government integration)
- Voice input/output via Sarvam AI (Indic language support)
- Responsive PWA (desktop + mobile)

---

## Architecture

```
Next.js PWA ──▶ FastAPI API ──▶ Domain Router (keyword + anchor + LLM)
                                       │
                                        ├─▶ Hybrid retrieval (dense pgvector + lexical)
                                        │       └─▶ optional reranker (wired, OFF by default)
                                        ├─▶ Web discovery (Tavily + SerpApi Google, bounded concurrent branches)
                                       ├─▶ Evidence gate (abstention if no citation)
                                       ├─▶ Citation verifier
                                       └─▶ Grounded LLM (Groq primary, Gemini fallback)
                                                └─▶ Answer / abstain + citations + confidence
                                       ▼
                                  Supabase Postgres + pgvector (HNSW)
```

The evidence gate runs **before** the LLM: the model can never upgrade a
low-confidence or uncited result into an answer.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 + React 19 + Tailwind CSS 4, PWA |
| Backend | FastAPI (Python 3.11+) |
| Database | Supabase Postgres + pgvector (HNSW cosine index) |
| Embeddings | Jina Embeddings v3 (`jina-embeddings-v3`), 768d |
| LLM | Groq (primary) + Gemini (fallback); model names come from `GROQ_MODEL` / `GROQ_FALLBACK_MODEL` / `GEMINI_MODEL` env (`config.py` defaults are empty; note `.env.example` still names the retired `llama-3.3-70b-versatile` — do not use it) |
| Reranker | Jina reranker (wired, disabled — `RERANKER_ENABLED=false`) |
| Voice | Sarvam AI (STT + TTS) primary, Azure fallback, text-only final |
| Document parsing | MinerU `content_list_v2.json` |
| Eval | Supabase-backed retrieval eval, pytest |

---

## Project Structure

```
backend/
  app/
    main.py                FastAPI entrypoint (8 routers: chat, voice, conversations, evidence, grievance, documents, translate, webhooks)
    routes/
      chat.py              /chat + /chat/stream — language detect → domain classify → RAGOrchestrator or GrievanceWorkflow
      voice.py             /voice, /voice/transcribe, /voice/speak
      conversations.py     /conversations/{session_id}
      evidence.py          /evidence/...
      grievance.py         /grievance REST endpoint
    domains.py             AnchorStore domain classifier (keyword rules + cosine similarity)
    evidence_gate.py       abstention thresholds (TOP1=0.25, SECONDARY=0.30, MIN_CHUNKS=2)
    citation_verifier.py   set-membership citation validation
    evidence_controller.py EvidenceBundle builder + curated prompt
    contracts.py           Pydantic models: EvidenceChunk, RAGResult, RAGResponse, etc.
    llm_fallback.py        grounded_answer() with Groq → Gemini fallback
    config.py              all settings and thresholds (pydantic-settings)
    providers/             groq_llm.py, gemini_llm.py, embeddings.py, reranker.py,
                           sarvam_voice.py, azure_voice.py, sarvam_translator.py, translator.py
    services/
      rag_orchestrator.py  async dual-pipeline (static + web), asyncio.gather, merge, generate, verify
      static_rag.py        Supabase pgvector hybrid retrieval (dense + lexical RRF)
      web_rag.py           10-step web RAG: Tavily/SerpApi-Google/Firecrawl → BM25 → Gemini rerank → source verify
      voice_service.py     STT/TTS with provider fallback (Sarvam → Azure)
      lang_memory.py       language preference memory
    grievance/             9-stage state machine
      workflow.py          GrievanceWorkflow (main orchestrator + Supabase persistence)
      classifier.py        complaint category classifier
      draft_builder.py     draft construction and update
      entity_extractor.py  entity extraction from user messages
      field_detector.py    missing field detection
      semantic_extractor.py semantic field extraction
      submission_guide.py  portal + submission route lookup
      status_lookup.py     status check guidance
      models.py            GrievanceState, GrievanceDraft, GrievanceTurn, etc.
    retrieval/             bm25_retriever.py, gemini_reranker.py, rrf.py
    web_rag/               service.py (WebDiscoveryService: bounded concurrent branches × provider fan-out, MAX_BRANCHES=8), query_classifier.py,
                           serpapi_client.py (SerpApi Google engine), tavily_client.py, firecrawl_client.py, providers.py (factory),
                           recovery.py (≤2 single-axis recovery rounds + cooperative `deadline`), referrals.py (abstention referrals),
                           mandate_map.py (+.json authority map), validity.py, leads.py, clusters.py, diversity.py,
                           facets.py, identifiers.py, impersonation.py, expansion.py (+.json), lexicon.py (+.json),
                           status.py, retrieval_scorer.py
    security/              source_verifier.py (trust-score filtering)
    data/                  keyword_rules.json, domain_anchors.json
  seed_parser.py           MinerU content_list_v2.json → canonical chunk JSONL
  ingest_seed.py           Jina-v3 embed + insert into Supabase
  schema.sql               Full Supabase schema (documents, chunks, sessions, grievance_states)
  tests/                   pytest test suite

frontend/
  src/app/
    page.tsx               Landing page (hero, stats, coverage, how it works)
    chat/                  Chat page
    grievance/             Grievance intake page
    schemes/               Schemes browser
    services/              Services browser
    library/               Document library
    faq/                   FAQ page
    legal/                 Legal info page
  src/components/
    ChatWindow.tsx          Main chat UI (SSE streaming, voice, citations)
    chat/                   Sub-components
    layout/                 Header, nav
    ui/                     Button, Badge, Icons, etc.
  src/lib/
    api.ts                  Backend API client
    i18n/                   11-language i18n provider + dictionaries
    data/                   schemes, services, library data
    speech.ts               Browser speech recording

corpus/
  seeds/json_files/        MinerU content_list_v2.json per source (ingestion input)

eval/                       Retrieval eval scripts + gold cases
workflows/                  Agent workflow loop docs (ingestion, retrieval, database, etc.)
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- Supabase project (free tier) with pgvector
- API keys: Supabase, Jina (embeddings), Groq, Gemini, Sarvam AI

### Backend

```bash
cd backend
cp ../.env.example .env      # fill in API keys
pip install -e .
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Environment Variables

See `.env.example`. Key variables:

```
SUPABASE_URL=             # Supabase project URL
SUPABASE_SERVICE_KEY=     # Supabase service role key
JINA_API_KEY=             # Jina embeddings
GROQ_API_KEY=             # Groq LLM
GEMINI_API_KEY=           # Gemini fallback LLM
SARVAM_API_KEY=           # Sarvam AI voice (STT + TTS) + translation
TAVILY_API_KEY_1=         # Tavily web search (dynamic RAG; _2 = rotation spare)
SERPAPI_API_KEY_1=        # SerpApi Google web search (_2 = fallback spare)
SEARCH_PROVIDERS=         # e.g. tavily,serpapi (.env.example default: tavily,firecrawl)
WEB_RAG_TIMEOUT_S=        # outer WebRAG budget in seconds (code default 30.0)
RERANKER_ENABLED=false    # reranker is wired but OFF (see PROJECT_STATUS)
CLERK_SECRET_KEY=         # Clerk auth (required for /chat, /voice, grievance-write)
CLERK_ISSUER=             # e.g. https://<instance>.clerk.accounts.dev
```

### Database Setup

Apply `backend/schema.sql` to your Supabase project via the SQL editor.

Tables created:
- `documents` — source documents with jurisdiction/state/domain metadata
- `chunks` — content chunks with `embedding vector(768)` + HNSW cosine index
- `sessions` — session state (jsonb)
- `grievance_states` — multi-turn grievance state (full GrievanceState as JSON)

RPC created: `match_chunks(query_embedding, match_domain, match_state, match_count)`

### Ingestion (corpus build)

Place each source's MinerU export at `corpus/seeds/json_files/<source>_content_list_v2.json`,
then:

```bash
python backend/seed_parser.py     # content_list_v2.json → chunks_jsonl/*.jsonl
python backend/ingest_seed.py     # embed (Jina v3) + insert into Supabase
```

Current frozen corpus: **11 documents, 4,778 embedded chunks (768d Jina v3)**.

---

## Supported Languages

| Language | Code | Status |
|---|---|---|
| English | `en` | Full support |
| Hindi | `hi` | Full support |
| Gujarati | `gu` | Full support |
| Marathi | `mr` | Full support |
| Bengali | `bn` | Full support |
| Tamil | `ta` | Full support |
| Telugu | `te` | Full support |
| Kannada | `kn` | Full support |
| Punjabi | `pa` | Full support |
| Odia | `or` | Full support |
| Malayalam | `ml` | Full support |

All UI strings translated. Backend responds in the same language as the question.

---

## RAG Pipelines

### Static RAG (Retrieval-Augmented Generation)
- Hybrid retrieval: dense vector search (pgvector) + lexical search (RRF fusion)
- Domain + jurisdiction filtering before candidate ranking
- Evidence gate with configurable thresholds
- Citation verification against retrieved chunks
- Abstains when evidence insufficient

### Dynamic RAG (Web-Grounded)
- Tavily + SerpApi Google web search for current information (bounded concurrent branches × provider fan-out; SerpApi Search Index evaluated 2026-10-05 and rejected)
- Bounded recovery: ≤2 single-axis rounds (`recovery.py`); cooperative deadline — no new round starts past `started + web_rag_timeout_s` (task cancellation can't stop the worker thread)
- Gemini reranker for semantic scoring
- Answer generation with Groq primary, Gemini fallback (model names from env)
- Language-matching: responds in same language as question

### Auth + frontend proxy
- `/chat`, `/chat/stream` (and grievance-write/voice) require a Clerk JWT → `401 {"detail":"Not authenticated"}` without it
- `BACKEND_API_URL` is the backend BASE (e.g. `http://localhost:8000`); `/api/chat`→`{base}/chat`, `/api/chat/stream`→`{base}/chat/stream`. A 503 means the backend is unreachable; restart `npm run dev` after changing `frontend/.env.local`

---

## Safety

- API keys are server-side only — never in frontend code (`NEXT_PUBLIC_*`).
- Every citation maps to a chunk actually retrieved in that request.
- Low retrieval confidence / no valid citation forces abstention.
- Jurisdiction + effective-date metadata on all legal/cooperative answers.
- Grievances are prototype-only (`is_official_submission: false`) — no real CPGRAMS.
- Structured logs without PII or secrets.

---

## License

Built for a hackathon. See repository for license details.
