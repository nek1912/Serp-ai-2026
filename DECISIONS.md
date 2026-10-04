# Decisions Log

Tracks deviations from original design or significant architectural choices.
When something is permanently changed, both this log AND the relevant doc are updated.

Each entry: what changed, why, what it replaced, when.

---

## Log

### Embedding model: Jina v3 as primary (not Gemini)
**Date:** 2026-08-26 → finalized  
**What:** `jina-embeddings-v3` (768d, task-typed) is the primary embedding model. Gemini embedding is the fallback. Configured via `embed_model` and `jina_embed_model` in `config.py`.  
**Why:** Jina v3 supports task-type differentiation (`retrieval.query` vs `retrieval.passage`), which improves asymmetric retrieval quality. Gemini embedding is kept as fallback.  
**Current state (from `app/providers/embeddings.py`):** JinaEmbeddingProvider primary, GeminiEmbeddingProvider fallback.

---

### Gemini embedding model: `gemini-embedding-2` (config default, overridable)
**Date:** 2026-08-26  
**What:** Config default is `gemini-embedding-2`. This is only used as fallback when Jina fails.  
**Note:** Earlier debates about `gemini-embedding-001` vs `gemini-embedding-2` are moot — Jina is primary. Gemini embedding is fallback only.

---

### Dropped NyayaSetu-Offline-Multilingual-AI as dependency
**Date:** 2026-08-26  
**What:** Grievance workflow is custom-built in `backend/app/grievance/`.  
**Why:** Could not verify the referenced repo (no stars, forks, or evidence of existence). Built from scratch with full control.

---

### Single Supabase instance for vectors + relational data
**Date:** 2026-08-26  
**What:** Supabase Postgres + pgvector for both chunk embeddings and grievance state.  
**Why:** One free-tier service vs two (separate Qdrant + Postgres). Simplifies ops.  
**Schema:** `documents`, `chunks` (vector 768d, HNSW cosine), `sessions`, `grievance_states`.

---

### Sarvam AI as primary voice + translation provider
**Date:** 2026-09-02  
**What:** `SarvamSTTProvider`, `SarvamTTSProvider`, `SarvamTranslator` are primary. Azure is fallback for STT only. Azure TTS is deliberately excluded.  
**Why:** Sarvam handles Indic languages (Hindi, Gujarati, Marathi, Bengali, Tamil) far better than Azure TTS. Azure TTS reads Indian languages as English gibberish.  
**Current state:** `VoiceService` in `app/services/voice_service.py`:
  - STT: Sarvam → Azure → `VoiceUnavailableError`
  - TTS: Sarvam only → `VoiceUnavailableError`

---

### Evidence gate thresholds
**Date:** 2026-09-02  
**What:** `TOP1_THRESHOLD=0.25`, `SECONDARY_THRESHOLD=0.30`, `MIN_CHUNKS_ABOVE_SECONDARY=2` in `config.py`.  
**Why:** Tests established these as the appropriate thresholds. Lower values were too permissive.

---

### Reranker wired but disabled
**Date:** 2026-09-02  
**What:** `RERANKER_ENABLED=false` in config. Jina reranker is wired in `StaticRAGService._apply_reranker()` but not called by default.  
**Why:** Enabling the reranker drops Recall@1 from 0.85 → 0.50 and Recall@5 from 0.975 → 0.875 on the current gold set. Do not enable without a new eval showing improvement.

---

### Out-of-scope queries abstain (not answered with disclaimer)
**Date:** 2026-09-02  
**What:** When `AnchorStore` classifies a query as `out_of_scope`, the system returns `abstained=True` with a scope message and no factual content.  
**Why:** The previous behavior generated a general answer with a disclaimer, which violated the core principle that the LLM is never the source of truth. Out-of-scope → abstain, not guess.  
**Current code:** `chat.py` lines ~286–300.

---

### Tamil added to ChatRequest.language
**Date:** 2026-09-02  
**What:** `language: Literal["en", "hi", "gu", "mr", "bn", "ta"]` — Tamil added.  
**Why:** Frontend i18n had Tamil (TA) but backend schema didn't, causing 422 errors.

---

### Dual-pipeline RAG: static + web run in parallel
**Date:** 2026-09-03 (finalized in RAGOrchestrator)  
**What:** `asyncio.gather` runs `StaticRAGService.retrieve` and `WebRAGService.retrieve` concurrently. Results merged into one `EvidenceBundle`.  
**Why:** Reduces latency; both sources inform the answer when available. Mode is `dual_rag`, `static`, or `web` based on which pipelines returned evidence.

---

### Auto-append citations when LLM omits them
**Date:** 2026-09-02  
**What:** `RAGOrchestrator._auto_append_citations()` appends `[chunk:XXXXXXXX]` markers from the top 3 chunks if the LLM output contains no citation markers at all.  
**Why:** Even with explicit citation instructions, some LLM calls omit markers. Fallback ensures the citation verifier has something to check. If truly no evidence, the gate would have abstained before reaching generation.

---

### GrievanceWorkflow persists state in Supabase
**Date:** 2026-09-03  
**What:** `grievance_states` table in Supabase. Full `GrievanceState` serialized to JSON, upserted on `conversation_id`.  
**Why:** Multi-turn grievance workflow requires server-side state across HTTP requests. Session ID / conversation ID is the key.

---

### Streaming endpoint uses word-level token emission
**Date:** 2026-09-04  
**What:** `/chat/stream` splits the final answer by spaces and emits each word as a `token` SSE event.  
**Why:** Groq does not support true streaming in the current integration. Word-level pseudo-streaming gives a streaming feel without needing true token streaming from the LLM provider.

---

### TTS uses `speech_text` not `answer`
**Date:** 2026-09-04  
**What:** Voice route passes `rag_result.get("speech_text")` to TTS, never the raw `answer`.  
**Why:** The raw answer contains `[chunk:id]` citation markers. `speech_text` is the citation-stripped version produced post-verification. TTS of citation markers is nonsense audio.

---

### Bounded evidence/context layers
**Date:** 2026-09-05
**What:** EvidenceController caps the generation prompt to top 3 static + top 3 dynamic chunks (3000 chars each). ContextBuilder defaults to max 8 chunks total. Web RAG caps at 12 chunks per source. These are independent layers — retrieval returns more, but each downstream stage further bounds what it processes.
**Why:** Intentional engineering control to bound context size and generation latency/cost while retaining sufficient evidence for grounded answers. Transplanted from eGovAssistant proven defaults.

---

### Bounded generation output
**Date:** 2026-09-05
**What:** `GENERATION_MAX_TOKENS = 1800` for normal generation, `REPAIR_MAX_TOKENS = 2200` for citation repair. These values are sent to Groq as `max_tokens` in the API request.
**Why:** Intentional engineering control to bound generation output size and latency/cost. Value transplanted from eGovAssistant proven defaults.

---

### Grievance localization: output-boundary translation, not LLM translation
**Date:** 2026-09-10
**What:** All grievance workflow processing happens in English only. Translation is applied at the output boundary in `_process_grievance_message()` and `/grievances/*` endpoints. User-entered values are preserved verbatim; only system-generated metadata (submission data, field labels, draft summary values, canonical dict top-level) is translated via the provider chain. Field labels use a static `FIELD_LABELS` lookup dict (150+ entries) rather than LLM translation.
**Why:** LLM translation of user values risks hallucination or content drift. Static lookup for field labels avoids latency and cost of per-request LLM calls. Output-boundary translation ensures English-only internal state while delivering fully localized responses.
**Current state:** `chat.py` and `grievance.py` both apply translation before returning responses. Frontend uses `field_label` from backend for tab rendering.

---

### Session isolation via useRef in ChatWindow
**Date:** 2026-09-10
**What:** `sessionId` in `ChatWindow.tsx` changed from `useState` to `useRef` + `resetSessionId()`. Reset on new-chat, load-conversation, delete-conversation, clear-all-history, and URL query param handlers.
**Why:** `useState` created `sessionId` once and never reset it, causing grievance state to leak across "New Chat" actions. Backend fresh-state defense in `GrievanceWorkflow.process_message()` complements this by detecting new complaints after completed grievances.
