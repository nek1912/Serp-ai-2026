# Project Status

**This is the one file every AI session and every team member reads first.**
The actual code files are the ground truth. This file reflects what is
currently implemented and working. When in doubt, read the code.

Update this at the end of every work session. An out-of-date status file is
worse than none — the next session will trust it.

---

## Last updated

`2026-09-18` — **Final answer presentation and WebRAG provider cleanup:** (1) Enhanced the existing Markdown renderer for answer headings, section rules, blockquotes, tables, links, and mobile-readable spacing without changing answer content, (2) Tavily API keys are attempted concurrently so a slow key cannot block a healthy replacement, (3) live WebRAG discovery returned 20 official results in approximately 15 seconds with the updated environment; Firecrawl remains a non-blocking fallback but returns HTTP 402 due exhausted credits, (4) focused backend checks passed: 42 tests; frontend targeted chat checks: MessageBubble passed, EvidencePanel has 5 pre-existing failures; frontend production build is blocked by existing missing `lenis` dependency/type errors.

`2026-09-17` — **Chat output and WebRAG cleanup:** (1) Fixed `req.mode` being passed as a Groq model name (`rag_web`), which caused a 404 and made the requested pipeline fall back unnecessarily; pipeline mode is now passed separately, (2) strengthened the existing answer prompt to require the selected-language script, direct answer plus headings/bullets/short paragraphs, and preserved official terms, (3) WebRAG provider failures now log the provider and bounded error instead of being silent, (4) live discovery probe returned 20 official PMFBY results; Firecrawl remains unavailable because its account returned HTTP 402 insufficient credits, (5) focused verification: 94 tests passed, final WebRAG/evidence checks: 32 passed.

`2026-09-17` — **Output formatting cleanup:** Preserved paragraph and markdown separation when Sarvam translates long final answers split across API-sized chunks. The tested `modern-colloquial` Sarvam mode and English grounding boundary remain unchanged. Focused output tests: 59 passed; broader chat/language/translation tests: 60 passed.

`2026-09-17` — **RAG V3 performance and translation boundary fixes:** (1) WebRAG total budget reduced from 90s to configurable 15s with cancellation and awaited cleanup on timeout; static retrieval continues independently, (2) Tavily and Firecrawl default request timeouts bounded to 5s, (3) Gemini reranker timeout configurable at 8s with AFC disabled; Jina fallback timeout configurable at 5s, (4) reranker provenance now records `reranker_used` and `reranker_fallback_reason`, (5) final reranking logs identify Jina fallback correctly, (6) Sarvam translation calls are stage-tagged (`input_query`, `final_answer`, `other`) and the SSE route now enforces the same single final-answer translation boundary as sync chat, (7) citation markers are protected during final translation, (8) timing telemetry added for query translation, static retrieval, WebRAG, generation, grounding, final translation, and total request, (9) WebRAG now uses its discovered-result BM25 ranker instead of the stale 1000-entry static snapshot, preserving the existing ranking call contract, (10) focused regression tests pass: 61 translation/reranker tests and 25 WebRAG/reranker tests; full backend suite: 1055 passed, 28 pre-existing failures, 2 deselected.

`2026-09-17` — **Part 3: Strict Evidence-Grounded Answer Generation (RAG V3):** (1) Strengthened `_SOURCE_PRIORITY_PROMPT` with 8 new rules: evidence is only factual authority, preserve material terms exactly, no synonym substitution for enumerated facts, closed-world numbers/thresholds, no document section merging, explicit conflict handling, missing information stays missing, user-friendly language allowed but factual terms survive, (2) `detect_enumeration_question()` — regex-based detector for list/category/eligibility questions (EN, HI, GU keywords), (3) `answer_grounding.py` — new module with `UnsupportedClaim`, `GroundingResult`, `verify_answer_grounding()` using regex extraction (numbers, dates, entities, conditions) + optional LLM verification layer, (4) Wired grounding check into `RAGOrchestrator` after citation verification (Step 9.5), removes unsupported claims from answer, (5) Enumeration prompt injection — when `detect_enumeration_question()` matches, adds "ENUMERATION MODE" instruction to user prompt, (6) `test_evaluation_grounding.py` — 12 evaluation test cases based on C01/D06/S08 failure patterns, (7) `ANSWER_GROUNDING_LLM_ENABLED` config flag (default False) for optional LLM verification layer, (8) All tests pass: 245/245 Part 3 tests; 5 pre-existing failures (embedding URL mock mismatch in `test_contract.py` and `test_evidence_gate_pipeline.py`); ruff lint clean; mypy not installed.

`2026-09-17` — Full i18n for all pages: (1) Added ~56 new i18n keys to dictionaries.ts covering schemes detail, services listing/detail, and legal listing/detail pages — all 11 languages, (2) Replaced all hardcoded English strings in `schemes/[slug]/page.tsx` (13 strings: Active badge, section headers like What is it, Who can apply, What do you get, How to apply, Key benefits, Document checklist, Ask AI, Have questions, AI help text, Start conversation), (3) Replaced hardcoded strings in `services/page.tsx` (category labels: All, Credit, Storage, Insurance, Agro services, Subsidy, Membership → now use `labelKey` in CATEGORY_META), (4) Replaced hardcoded strings in `services/[slug]/page.tsx` (11 strings: not found, back to services, Quick Facts, Access, Benefits, Source, How to join, What you get, Have questions, AI help, Start conversation), (5) Replaced hardcoded strings in `legal/page.tsx` (category labels: All, Acts, Bye-Laws, Provisions), (6) Replaced hardcoded strings in `legal/[slug]/page.tsx` (12 strings: not found, back to legal, All legal documents, Quick Facts, Type, Key Provisions, provision count, Applicability, Source, Related documents, Have questions, AI help, Start conversation), (7) Added native I18nText translations for 3 previously untranslated services (pm-fb-enrollment, cooperative-training, digital-banking) — all 11 languages for name, summary, description, whoCanUse, howToAccess, source.label, (8) Fixed TS errors: removed duplicate dictionary keys (44 lines), removed impossible `"all"` comparisons in legal page (LegalCategory doesn't include "all").

`2026-09-17` — Language expansion (6→11) + model routing fix: (1) Added 5 new Sarvam-supported languages — Telugu (te), Kannada (kn), Punjabi (pa), Odia (or), Malayalam (ml) — wired existing dictionaries into `LOCALES`, `LanguageSwitcher`, and `dict` export, (2) Updated backend: `ChatRequest.language` Literal expanded to 11 langs, `language_config.json` updated with scripts/stopwords/supported_languages for all 11, `language.py` updated `_SCRIPT_TO_LANG` (9 scripts), `_EXPLICIT_LANG_NAMES` (11 langs), `_EXPLICIT_RE` regex, `normalize_language()` detection for tamil/telugu/kannada/gurmukhi/odia/malayalam scripts, `detect_query_languages()` presence+dominant detection for all 9 Indic scripts, `english_retrieval_query()` now handles all 9 scripts, (3) Added `_THINKING_MESSAGES` and `_STEP_LABELS` for te/kn/pa/or/ml in chat.py, (4) Model routing fix: V1 (static) now runs only StaticRAGService, V2 (web) now runs only WebRAGService, V3 (rag_web) unchanged — both pipelines in parallel; `mode` from `ChatRequest` now passed to `RAGOrchestrator.run()` as `model_override`, orchestrator's `_run_pipelines()` conditionally executes pipelines based on mode.

`2026-09-17` — Brand consistency + full i18n overhaul: (1) Unified brand name to "JanSahay" across all files — fixed "JanSayah" typo in dictionaries.ts (chat.home, chat.user, chat.disclaimer, taglines, footer email), "Sahkarita" in FloatingChatWidget greeting, "eGovAssistant" in grievance draft warnings, (2) Fixed localized tagline translations — GU (સહકારિતા→JanSahay), BN (সহকারিতা→JanSahay), TA (சகாரிதா→JanSahay), (3) i18n'd FloatingChatWidget — greeting, subtitle, open full chat, placeholder, error message all use `t()` with 6-language keys, (4) i18n'd homepage BENTO_CARDS — 6 feature cards (Multilingual, Voice-enabled, Evidence-backed, 6 Languages, Secure & Private, Guided Next Steps) now use `t()` with new `landing.bento1-6title/text` keys in all 6 languages, (5) i18n'd reviews section title — "Trusted by Cooperative Members Across India" now uses `t("landing.reviewsTitle")` with translations, (6) Fixed ChatWindow localStorage keys from `jansayah_` to `jansahay_`, (7) Fixed footer email domain from `jansayah.gov.in` to `jansahay.gov.in`.

`2026-09-15` — Full responsive overhaul: (1) Homepage hero removed hardcoded `ml-36`, added responsive padding/text/CTA/trust badge sizing, (2) Library page added missing `px-*` padding, (3) ArcCarousel rewritten with mobile-first stacked card layout (220px) + desktop 3D carousel (320px), (4) Stepper hidden labels on small screens with smaller circles, (5) Grievance classification replaced table with stacked flex layout for mobile, (6) Grievance Status page added padding + stacked search input/button, (7) Grievance Draft View added responsive padding, (8) FloatingChatWidget responsive width `w-[calc(100vw-2rem)] max-w-[380px]`, (9) All detail pages (schemes, services, legal) updated to `px-4 sm:px-6 md:px-12` pattern, (10) FAQ page responsive padding.

`2026-09-15` — Added thinking process animation: (1) `on_step` callback in `RAGOrchestrator.run()` emits structured step events at each pipeline stage, (2) `_STEP_LABELS` localized labels (6 languages × 6 step IDs) + `_make_step_emitter()` in `chat.py`, (3) New `StepEvent` type + `"step"` SSE event in `api.ts`, (4) `ThinkingProcess` component replaces `ThinkingBubble` — step list with spinner/checkmark, auto-collapse on token arrival, dropdown chevron to re-expand, (5) Wired into `ChatWindow` and `FloatingChatWidget` with `thinkingSteps` state + `step` event handler, (6) 7 new ThinkingProcess tests, 4 updated ChatWindow tests, all passing.

## Current state

System is **feature-complete**. Backend RAG pipeline, 9-stage grievance workflow,
voice I/O, multi-language support (6 languages), and Next.js frontend are all
implemented and wired together.

**Selected state:** `gujarat` (`selected_state: "gujarat"` in `backend/app/config.py`)

**LLM config (locked):** Primary=`openai/gpt-oss-120b` (Groq), Fallback=`qwen/qwen3.8-27b` (Groq), Ultimate=Gemini `gemini-2.5-flash`

---

## Component status

`not started / stubbed / in progress / working / broken`

| Component | File(s) | Status | Notes |
|---|---|---|---|
| FastAPI app + `/health`, `/health/providers` | `app/main.py` | working | 6 routers registered (including documents) |
| `/chat` (sync) | `app/routes/chat.py` | working | Language detect → domain classify → RAGOrchestrator or GrievanceWorkflow; clean language boundary for grievance (input translate → English workflow → output translate); multilingual routing with Tier 2 grievance-keyword override + Tier 3 non-English guard; 11-language support (en, hi, gu, mr, bn, ta, te, kn, pa, or, ml); mode-aware routing (static/web/rag_web) |
| `/chat/stream` (SSE) | `app/routes/chat.py` | working | Same pipeline, Server-Sent Events with `thinking/step/token/metadata/done` events; step events show pipeline progress; same multilingual routing fixes |
| `/voice`, `/voice/transcribe`, `/voice/speak` | `app/routes/voice.py` | working | Full audio→STT→RAG→TTS pipeline |
| `/conversations` | `app/routes/conversations.py` | working | Session history retrieval |
| `/evidence` | `app/routes/evidence.py` | working | Evidence endpoint |
| `/grievance` (route) | `app/routes/grievance.py` | working | Grievance REST endpoint |
| `/documents/pdf/{filename}` | `app/routes/documents.py` | working | Safe PDF serving with path traversal prevention |
| Domain classifier (AnchorStore) | `app/domains.py` | working | Keyword rules + embedding cosine; floor 0.30 |
| Session store | `app/session_store.py` | working | Supabase-backed, keeps last 50 messages; frontend resets on new-chat/load/delete |
| Language detection | `app/language.py` | working | Detects dominant language and language mix; supports 11 languages (en, hi, gu, mr, bn, ta, te, kn, pa, or, ml) with 9 Indic scripts |
| Translation (Sarvam primary, Azure fallback) | `app/providers/sarvam_translator.py`, `app/providers/translator.py` | working | Used in chat.py pre/post RAG |
| RAGOrchestrator | `app/services/rag_orchestrator.py` | working | Mode-aware: V1/V2/V3 pipeline routing; evidence merge, prompt building, LLM generation; includes `_complexity_classifier` for SIMPLE/COMPLEX query routing |
| QueryComplexityClassifier | `app/scenario_reasoning.py` | working | Regex + keyword classifier: SIMPLE vs COMPLEX (procedures, eligibility, comparisons, multi-hop, ambiguous) |
| ScenarioPlanner | `app/scenario_reasoning.py` | working | LLM-based planner: extracts structured requirements + missing user facts; JSON fallback on LLM failure |
| EvidenceMapBuilder | `app/scenario_reasoning.py` | working | Merges retrieval results into unified evidence map with dedup, section coverage, multi-document tracking |
| DerivedConclusionEngine | `app/scenario_reasoning.py` | working | Generates conclusions from evidence-supported requirements only |
| Answer grounding | `app/answer_grounding.py` | working | Regex claim extraction, condition extraction, enumeration detection; verifies facts against evidence |
| StaticRAGService | `app/services/static_rag.py` | working | Supabase pgvector hybrid retrieval (dense + lexical RRF) → EvidenceChunks |
| WebRAGService | `app/services/web_rag.py` | working | 10-step web RAG: Tavily/Firecrawl → BM25 → Gemini pre-rank → RRF → Gemini final-rank → source verify → EvidenceChunks |
| EvidenceController + prompt builder | `app/evidence_controller.py` | working | Merges chunks, builds curated source-priority prompt |
| Evidence gate | `app/evidence_gate.py` | working | Threshold: `TOP1_THRESHOLD=0.25`, `SECONDARY_THRESHOLD=0.30`, `MIN_CHUNKS_ABOVE_SECONDARY=2` |
| Citation verifier | `app/citation_verifier.py` | working | Set-membership check — every `[chunk:id]` must map to a retrieved chunk; web IDs `web_*` supported; ambiguity-safe prefix matching |
| Confidence calculation | `app/services/rag_orchestrator.py` | working | Band-based; dual-source gets +0.10 boost |
| GrievanceWorkflow (9-stage state machine) | `app/grievance/workflow.py` | working | INTAKE → CLASSIFICATION → ENTITY_EXTRACTION → MISSING_FIELDS → FOLLOWUP → DRAFT_READY → SUBMISSION_GUIDE → STATUS_LOOKUP → COMPLETE; English-only source of truth; fresh-state defense for new complaints after completed grievance |
| Grievance classifier | `app/grievance/classifier.py` | working | |
| Grievance draft builder | `app/grievance/draft_builder.py` | working | English-only; canonical dict exposes `description.original` for user's pre-translation text |
| Grievance entity extractor | `app/grievance/entity_extractor.py` | working | |
| Grievance field detector | `app/grievance/field_detector.py` | working | `FIELD_LABELS` dict (150+ entries), `get_field_labels()` for i18n field names; prompts translated via `translate_field_prompt()` using `FIELD_PROMPTS` map |
| Grievance semantic extractor | `app/grievance/semantic_extractor.py` | working | |
| Grievance submission guide | `app/grievance/submission_guide.py` | working | Portal lookup; output translated at boundary |
| Grievance status lookup | `app/grievance/status_lookup.py` | working | |
| Grievance state persistence | `app/grievance/workflow.py` | working | Supabase `grievance_states` table, upsert on `conversation_id` |
| Grievance localization layer | `app/routes/chat.py`, `app/routes/grievance.py`, `app/grievance/translations.py` | working | Backend translates: field prompts (FIELD_PROMPTS map), submission steps, followup prefixes, workflow prefixes, field labels, draft_summary, canonical dict; user values preserved verbatim; frontend translates field card labels via i18n dictionary |
| Routing hierarchy | `app/routes/chat.py`, `app/grievance/workflow.py` | working | Tier 1: domain/intent → grievance; Tier 2: guidance intent → RAG, grievance-keyword override; Tier 3: non-English guard; informational regex |
| Grievance UI (frontend) | `frontend/src/components/chat/GrievanceFlow.tsx` | working | Orchestrates stage panels; `GrievanceClassificationPanel`, `GrievanceFieldPanel`, `GrievanceCard` (renders i18n-translated field labels) |
| Session isolation | `frontend/src/components/ChatWindow.tsx` | working | `useRef` + `resetSessionId()` prevents state leakage across "New Chat" |
| VoiceService (STT/TTS fallback chain) | `app/services/voice_service.py` | working | STT: Sarvam→Azure; TTS: Sarvam only (Azure bad for Indic langs) |
| Sarvam STT/TTS providers | `app/providers/sarvam_voice.py` | working | Primary voice provider |
| Azure STT fallback | `app/providers/azure_voice.py` | working | Fallback STT only |
| Embeddings (Jina primary, Gemini fallback) | `app/providers/embeddings.py` | working | Jina v3 768d, task-typed (`retrieval.query` / `retrieval.passage`) |
| Jina reranker | `app/providers/reranker.py` | working | Wired in StaticRAGService but **disabled** (`RERANKER_ENABLED=false`) |
| Gemini LLM provider (fallback) | `app/providers/gemini_llm.py` | working | `_classify_error()` for structured error classification; tiered model fallback |
| Groq LLM provider (primary) | `app/providers/groq_llm.py` | working | Primary=`openai/gpt-oss-120b`, Fallback=`qwen/qwen3.8-27b`; key rotation + `_classify_error()` |
| Sarvam chat provider | `app/providers/sarvam_chat.py` | working | |
| Web discovery (Tavily / Firecrawl) | `app/web_rag/service.py` | working | |
| Query classifier (web RAG) | `app/web_rag/query_classifier.py` | working | Domain, jurisdiction, state classification for web queries |
| Source verifier | `app/security/source_verifier.py` | working | Trust-score based filtering in web RAG |
| Next.js frontend (PWA) | `frontend/` | working | Next.js 16, React 19, Tailwind v4, GSAP; fully responsive (320px–desktop) |
| Frontend pages | `frontend/src/app/` | working | `/` (home), `/chat`, `/grievance`, `/schemes`, `/services`, `/library`, `/faq`, `/legal` — all with responsive padding and mobile-first layouts; homepage bento cards + reviews section fully i18n'd |
| Frontend i18n (11 languages) | `frontend/src/lib/i18n/` | working | EN, HI, GU, MR, BN, TA, TE, KN, PA, OR, ML; includes field label translations (45 keys per locale) for grievance card rendering; bento card titles/texts (6 cards × 2 keys), reviews title, widget strings (greeting/subtitle/openFull/placeholder/error) |
| ChatWindow (streaming SSE) | `frontend/src/components/ChatWindow.tsx` | working | Handles `thinking/step/token/metadata/done` SSE events, voice recording, citation display; localStorage keys use `jansahay_` prefix |
| Thinking Process UI | `frontend/src/components/chat/ThinkingProcess.tsx` | working | Step-by-step reasoning display with auto-collapse and dropdown re-expand; replaces ThinkingBubble |
| Evidence Panel | `frontend/src/components/chat/MessageBubble.tsx` | working | Unified `EvidencePanel` + `EvidenceCard` components; `data-evidence="true"` attribute; citation tags with `aria-expanded`/`aria-label`; keyboard-focusable; scroll-into-view on expand |
| Document ingestion pipeline | `backend/seed_parser.py`, `backend/ingest_seed.py` | working | Parses MinerU `content_list_v2.json` → JSONL → embeds → Supabase |
| Database schema | `backend/schema.sql` | working | `documents`, `chunks` (vector 768d, HNSW), `sessions`, `grievance_states` |

---

## Provider status

| Provider | Status | Role |
|---|---|---|
| Groq | configured | Primary LLM (key rotation supported) |
| Gemini | configured | Fallback LLM + Gemini reranker in WebRAGService + grievance model |
| Jina | configured | Primary embeddings (jina-embeddings-v3, 768d) |
| Supabase | configured | Postgres + pgvector (HNSW cosine), sessions, grievance_states |
| Sarvam AI | configured | Primary STT, TTS, translation (key rotation supported) |
| Tavily | configured | Primary web search |
| Firecrawl | configured | Web crawl / scrape fallback |
| Azure Cognitive Services | configured | Fallback STT only (TTS deliberately excluded — bad Indic output) |
| Bhashini / ULCA | stubbed | `bhashini_stub.py` present, not wired |
| Render | configured | Backend hosting (`render.yaml`) |

---

## Known issues / caveats

- **Reranker disabled**: `RERANKER_ENABLED=false` in config. When enabled, Recall@1 drops 0.85→0.50. Keep off.
- **Agriculture corpus missing**: `agriculture` domain routes to `out_of_scope` — no docs ingested, not a bug.
- **Gold eval set is retriever-anchored**: Recall metrics are optimistic (measures if retriever surfaces its own best-matching chunk, not true answer span). Manual curation needed for production eval.
- **Supabase free-tier pausing**: May pause after inactivity. Reactivate before demos.
- **Dead Groq model**: `llama-3.3-70b-versatile` is DEAD on Groq (HTTP 404). Use `openai/gpt-oss-120b` (primary) and `qwen/qwen3.8-27b` (fallback) only.
- **MoC_Young_Professionals_YPs.pdf**: Scanned/image-based PDF processed via EasyOCR + pymupdf. OCR text is lower quality than MinerU extraction. 213 chunks embedded (611 raw from OCR).
- **Pre-existing test failures (backend)**: ~4 tests fail in `test_chat_route_refactored.py` due to `_has_active_grievance()` hitting unmocked Supabase `grievance_states` endpoint (not related to routing or localization bugs). Down from ~40 after routing fixes.
- **Pre-existing test failures (frontend)**: 7 tests (4 ChatWindow sendChat mock, 1 LOCALES expectation, 1 Gujarati missing key, 1 read-aloud button test).
- **PDF endpoint**: Regex allows `A-Za-z0-9_\-\.(), ` for filenames. Path traversal blocked. Non-PDF extensions rejected.
- **`chunks.source_file` column**: Does NOT exist in live Supabase DB. `source_file` is stored in `chunks.metadata` JSONB instead.

---

## Corpus

All ingested from `corpus/seeds/json_files/*_content_list_v2.json`
via `backend/seed_parser.py` → `backend/ingest_seed.py`. Embeddings: Jina v3 768d.

| Domain | Chunks embedded | Source |
|---|---|---|
| pacs_governance | 1294 | Model Byelaws (337) + HR Policy V21 (601) + MoC YPs (213) + CSM Scheme (43) |
| pacs_computerization | 214 | Revised Scheme guidelines (192) + Corrigendum (22) |
| pmfby | 1266 | operational_guidelines_pmfby |
| financial_inclusion | 2004 | NSFI_2025_30 (371) + RBI FAME (579) + RBI BE(A)WARE (429) + IRDAI Insurance (725) |

**Total: 11 documents, 4778 embedded chunks, 768d Jina v3**

### PDF provenance chain

```
PDF (corpus/seeds/*.pdf)
  -> pypdf extraction -> corpus/seeds/chunks_jsonl/*.jsonl (source_file per chunk)
    -> ingest_seed.py (DOC_META -> Supabase documents + chunks.metadata)
      -> StaticRAGService -> EvidenceChunk.metadata["source_file"]
        -> _build_citations() -> citation["source_file"]
          -> Frontend EvidenceCard -> /api/documents/pdf/{source_file}#page={page}
```

---

## Flagship demos

1. **Hindi PMFBY voice query** — Audio → Sarvam STT → RAGOrchestrator → Sarvam TTS audio response
2. **Cooperative/PACS state-filtered question** — `state="gujarat"` filtered in `static_rag.py` pgvector queries
3. **Grievance intake + status lookup** — Multi-turn intake → entity extraction → prototype reference (`DEMO-PACS-xxxxx`) → status lookup guidance; supports 6 languages via clean input/output translation boundary
