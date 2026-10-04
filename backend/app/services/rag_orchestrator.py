"""RAG Orchestrator — async dual-pipeline RAG with evidence bundle.

Runs static RAG (Supabase pgvector) and web RAG (Tavily/Firecrawl) in
parallel via asyncio.gather, merges evidence chunks into an EvidenceBundle,
builds a curated source-priority prompt, generates an answer via LLM with
provider fallback, verifies citations, calculates confidence,
and returns a typed RAGResponse.

Architecture:
  1. asyncio.gather runs both pipelines concurrently (return_exceptions=True)
  2. EvidenceController builds an EvidenceBundle + curated prompt
  3. LLM generates answer (Groq primary, Gemini fallback)
  4. Citations are verified
  5. Confidence is calculated
  6. RAGResponse is returned
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable

from app.answer_grounding import verify_answer_grounding
from app.citation_verifier import verify_citations
from app.config import Settings, get_settings
from app.contracts import (
    AbstentionReason,
    ConfidenceBand,
    EvidenceChunk,
    RAGResponse,
    RAGResult,
)
from app.evidence_controller import EvidenceController, QueryRequirementClassifier, strip_citations, clean_answer
from app.llm_fallback import AllProvidersFailedError, grounded_answer
from app.scenario_reasoning import QueryComplexityClassifier
from app.providers.gemini_llm import GeminiLLMProvider
from app.providers.groq_llm import GroqLLMProvider
from app.providers.sarvam_chat import SarvamChatProvider
from app.services.static_rag import StaticRAGService
from app.services.web_rag import WebRAGService
from app.speech_text import prepare_speech_text, segment_speech
from app.ui import get_abstain_text
from app.web_rag.query_classifier import QueryClassification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RAGOrchestrator implementation
# ---------------------------------------------------------------------------

_BAND_TO_CONFIDENCE: dict[str, float] = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.4,
}


def fix_broken_tables(answer: str) -> str:
    """Detect and fix broken markdown table formatting.
    
    The LLM sometimes outputs pipe-separated text that isn't valid
    markdown table syntax. This function:
    1. Identifies lines that look like broken tables (have | but no proper header/separator)
    2. Attempts to reconstruct them as proper tables
    3. Falls back to stripping pipe characters if reconstruction fails
    """
    lines = answer.split('\n')
    result = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check if this line starts a potential table (or is an inline broken table)
        if '|' in line:
            # Collect consecutive lines with |
            table_lines = []
            j = i
            while j < len(lines) and '|' in lines[j]:
                table_lines.append(lines[j])
                j += 1
            
            # Check if it's a valid table (has separator row)
            has_separator = any(re.match(r'^\s*\|[\s\-|]+\|\s*$', tl) for tl in table_lines)
            
            if has_separator and len(table_lines) >= 3:
                # Valid table — keep as is
                result.extend(table_lines)
            else:
                # Broken table — strip pipes and convert to bullet list
                for tl in table_lines:
                    cleaned = tl.replace('|', ' ').strip()
                    cleaned = re.sub(r'\s+', ' ', cleaned)
                    if cleaned and cleaned != '---':
                        result.append(f"- {cleaned}")
            
            i = j
        else:
            result.append(line)
            i += 1
    
    return '\n'.join(result)


class RAGOrchestrator:
    """Runs async dual-pipeline RAG with evidence bundling and claim verification.

    Usage:
        orchestrator = RAGOrchestrator(settings)
        response = await orchestrator.run(
            query="What is PMFBY?",
            english_query="What is PMFBY?",
            embedding=[0.1] * 768,
            domain="pmfby",
            state="gujarat",
            classification=classification,
            history=[],
            lang="en",
            session_id="sess-123",
        )
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._static_rag = StaticRAGService(self._settings)
        self._web_rag = WebRAGService()
        self._evidence_controller = EvidenceController()
        self._query_classifier = QueryRequirementClassifier()
        self._complexity_classifier = QueryComplexityClassifier()

    async def run(
        self,
        query: str,
        english_query: str,
        embedding: list[float],
        domain: str,
        state: str | None,
        classification: QueryClassification | None,
        history: list[dict] | None,
        lang: str,
        session_id: str,
        language_mix: dict[str, float] | None = None,
        model_override: str | None = None,
        pipeline_mode: str | None = None,
        on_step: Callable[[dict], None] | None = None,
    ) -> RAGResponse:
        """Execute the full async dual-pipeline RAG flow.

        Args:
            query: Original user query (may be non-English).
            english_query: Translated English query for retrieval.
            embedding: Query embedding vector.
            domain: Detected domain from AnchorStore.
            state: Resolved state for jurisdiction filtering.
            classification: Query classification from QueryClassifier.
            history: Conversation history turns.
            lang: Response language code.
            session_id: Session identifier.
            language_mix: Optional dict mapping language codes to their
                proportion in the mixed-language query.
            model_override: Optional LLM model name to override the default.
            on_step: Optional callback invoked with progress dicts
                (``{"id": ..., "detail": ..., "status": ...}``) at each major
                pipeline stage so callers can stream step-by-step updates.

        Returns:
            RAGResponse with answer, citations, confidence, and speech data.
        """
        self._user_lang = lang
        logger.info(
            "RAGOrchestrator.run: domain=%s state=%s lang=%s session=%s",
            domain, state, lang, session_id,
        )

        if classification is None:
            classification = self._web_rag.web_discovery.classifier.classify(english_query, default_state=state)

        if (not domain or domain == "general") and classification and classification.domain != "general":
            domain = classification.domain

        # Step 1: Classify query requirements
        query_requirements = self._query_classifier.classify(query, lang)

        # Step 2: Run pipelines based on mode
        static_result, web_result = await self._run_pipelines(
            english_query=english_query,
            embedding=embedding,
            domain=domain,
            state=state,
            classification=classification,
            mode=pipeline_mode or "rag_web",
        )
        web_total_ms = float(web_result.metadata.get("web_total_ms", 0.0))

        if on_step:
            static_count = len(static_result.chunks) if not static_result.abstained else 0
            web_count = len(web_result.chunks) if not web_result.abstained else 0
            if static_count > 0:
                on_step({"id": "static_done", "detail": f"Found {static_count} chunks from official documents", "status": "completed"})
            if web_count > 0:
                on_step({"id": "web_done", "detail": f"Found {web_count} results from web sources", "status": "completed"})

        has_static = not static_result.abstained and len(static_result.chunks) > 0
        has_web = not web_result.abstained and len(web_result.chunks) > 0

        logger.info(
            "Pipeline results: static=%d chunks (abstained=%s), web=%d chunks (abstained=%s)",
            len(static_result.chunks), static_result.abstained,
            len(web_result.chunks), web_result.abstained,
        )

        # Step 3: If both pipelines abstained, return abstain response
        if not has_static and not has_web:
            return self._abstain_response(
                lang=lang,
                reason=static_result.reason or web_result.reason or AbstentionReason.NO_ELIGIBLE_SOURCE,
                domain=domain,
                session_id=session_id,
            )

        # Step 4: Build evidence bundle
        if on_step:
            on_step({"id": "retrieval_start", "detail": "Retrieval complete", "status": "completed"})
            on_step({"id": "evidence_merge", "detail": "Merging and ranking evidence from both sources", "status": "active"})
        bundle = self._evidence_controller.build_bundle(
            static_result, web_result, query_requirements, query,
        )

        # Step 5: Assess evidence
        assessment = self._evidence_controller.assess_evidence(
            static_result, web_result, query_requirements,
        )

        # Step 6: Build curated prompt with source-priority rules
        _t_prompt_start = time.monotonic()
        system_prompt, user_prompt = self._evidence_controller.build_curated_prompt(
            bundle, english_query, history, "en",
            language_mix=language_mix,
            assessment=assessment,
        )
        _t_prompt_ready = time.monotonic()
        prompt_build_ms = (_t_prompt_ready - _t_prompt_start) * 1000

        # Step 7: Generate answer via LLM (Groq primary → Gemini fallback → Sarvam tertiary)
        # Always use the primary model; the provider's own fallback iteration
        # handles model list traversal (GPT-OSS → Qwen → Gemini → Sarvam).
        if on_step:
            on_step({"id": "evidence_merge", "detail": "Evidence merged", "status": "completed"})
            on_step({"id": "llm_generate", "detail": "Generating grounded response from retrieved evidence", "status": "active"})
        model_name = model_override or self._settings.groq_model

        mode = "groq"
        try:
            primary_provider = GroqLLMProvider(self._settings)
            primary_provider._model = model_name  # Override for model_override support

            tertiary_provider = None
            try:
                if self._settings.sarvam_keys:
                    tertiary_provider = SarvamChatProvider(self._settings)
            except Exception as e:
                logger.warning("Could not initialize SarvamChatProvider for fallback: %s", e)

            _t_groq_start = time.monotonic()
            answer = grounded_answer(
                primary_provider,
                GeminiLLMProvider(self._settings),
                system_prompt,
                user_prompt,
                tertiary=tertiary_provider,
            )
            _t_groq_done = time.monotonic()
            answer = answer.replace("\u3010", "[").replace("\u3011", "]")
        except AllProvidersFailedError:
            logger.exception("All LLM providers failed")
            return self._abstain_response(
                lang=lang,
                reason=AbstentionReason.PROVIDER_UNAVAILABLE,
                domain=domain,
                session_id=session_id,
            )

        # Step 8: Auto-append citations if missing and clean non-citation markers
        all_chunks = self._merge_evidence(static_result.chunks, web_result.chunks)
        answer = self._auto_append_citations(answer, all_chunks)

        # Step 9: Verify citations against evidence, with auto-repair
        all_chunk_ids = [chunk.chunk_id for chunk in all_chunks]
        citation_verification = verify_citations(answer, all_chunk_ids)
        if not citation_verification.is_valid:
            logger.warning(
                "Citation verification failed: reason=%s invalid_prefixes=%s — performing auto-repair",
                citation_verification.reason, citation_verification.invalid_prefixes,
            )
            # Repair invalid prefixes if present
            for prefix in citation_verification.invalid_prefixes:
                answer = re.sub(rf"\[chunk:\s*{prefix}[0-9a-fA-F]*\]", "", answer)
            
            # Re-ensure valid citations from retrieved chunks
            answer = self._auto_append_citations(answer, all_chunks, force=True)
            citation_verification = verify_citations(answer, all_chunk_ids)

            if not citation_verification.is_valid and not all_chunks:
                return self._abstain_response(
                    lang=lang,
                    reason=citation_verification.reason or AbstentionReason.CITATION_FAILURE,
                    domain=domain,
                    session_id=session_id,
                )

        # Step 9.5: Post-generation grounding check
        _t_grounding_start = time.monotonic()
        grounding_result = verify_answer_grounding(
            answer,
            all_chunks,
            use_llm_verification=self._settings.answer_grounding_llm_enabled,
            settings=self._settings,
        )
        if grounding_result.has_unsupported_claims:
            logger.warning(
                "Grounding check found %d unsupported claims: %s",
                len(grounding_result.unsupported_claims),
                [c.claim_text for c in grounding_result.unsupported_claims],
            )
            # Remove unsupported claims from answer
            for claim in grounding_result.unsupported_claims:
                # Try to remove the sentence containing the unsupported claim
                # Simple approach: remove the claim text and surrounding context
                answer = re.sub(
                    rf"[^.]*\b{re.escape(claim.claim_text)}\b[^.]*\.",
                    "",
                    answer,
                )
            # Clean up extra spaces
            answer = re.sub(r'  +', ' ', answer).strip()
        grounding_ms = (time.monotonic() - _t_grounding_start) * 1000

        if on_step:
            on_step({"id": "llm_generate", "detail": "Response generated", "status": "completed"})
            on_step({"id": "citation_verify", "detail": f"Verified {len(all_chunks)} citations against source documents", "status": "completed"})
        _t_citation_done = time.monotonic()

        # Step 10: Strip internal citation markers and fix formatting
        stripped_answer, _extracted_ids = strip_citations(answer)
        answer = clean_answer(stripped_answer)
        answer = fix_broken_tables(answer)

        # Step 11: Calculate confidence
        confidence, confidence_band = self._calculate_confidence(
            static_result, web_result, has_static, has_web,
            [],  # No claim verifications
        )

        # Step 12: Build citations list
        citations = self._build_citations(all_chunks)

        # Step 13: Prepare speech text
        speech_text = prepare_speech_text(answer)
        speech_segments = segment_speech(answer, lang)

        # Step 14: Determine mode
        if has_static and has_web:
            mode = "dual_rag"
        elif has_static:
            mode = "static"
        else:
            mode = "web"

        # Generation latency instrumentation
        _t_gen_end = time.monotonic()
        groq_http_ms = (_t_groq_done - _t_groq_start) * 1000
        citation_validation_ms = (_t_citation_done - _t_groq_done) * 1000
        generation_total_ms = (_t_gen_end - _t_prompt_start) * 1000

        # Prompt token estimation (~4 chars per token)
        _input_chars = len(system_prompt) + len(user_prompt)
        _input_tokens_est = _input_chars // 4
        _output_tokens_est = len(answer) // 4

        logger.info(
            "Generation timing: prompt_build=%.0fms groq_http=%.0fms citation_val=%.0fms total=%.0fms | "
            "model=%s input_chars=%d input_tokens~%d output_tokens~%d llm_calls=1 | "
            "citations=%d abstained=False",
            prompt_build_ms, groq_http_ms, citation_validation_ms, generation_total_ms,
            model_name, _input_chars, _input_tokens_est, _output_tokens_est,
            len(citations),
        )
        logger.info(
            "RAG timing: static_retrieval_ms=%.0f web_total_ms=%.0f generation_ms=%.0f grounding_ms=%.0f",
            float(static_result.metadata.get("retrieval_ms", 0.0)),
            web_total_ms,
            generation_total_ms,
            grounding_ms,
        )

        logger.info(
            "RAGOrchestrator response: confidence=%.2f band=%s mode=%s citations=%d",
            confidence, confidence_band.value, mode, len(citations),
        )

        return RAGResponse(
            answer=answer,
            language=lang,
            domain=domain,
            confidence=confidence,
            confidence_level=confidence_band,
            citations=citations,
            abstained=False,
            speech_text=speech_text,
            speech_segments=speech_segments,
            follow_up_question=None,
            mode=mode,
            conversation_id=session_id,
        )

    async def _run_pipelines(
        self,
        english_query: str,
        embedding: list[float],
        domain: str,
        state: str | None,
        classification: QueryClassification | None,
        mode: str | None = None,
    ) -> tuple[RAGResult, RAGResult]:
        """Run static and/or web RAG pipelines based on mode.

        Modes:
          - "static": Static RAG only (no web search)
          - "web": Web RAG only (no static retrieval)
          - "rag_web" or None: Both pipelines in parallel (default)
        """
        abstain = RAGResult(
            chunks=[], abstained=True,
            reason=AbstentionReason.NO_ELIGIBLE_SOURCE, domain=domain,
        )

        # Static-only mode (V1)
        if mode == "static":
            started = time.monotonic()
            try:
                static_result = await asyncio.to_thread(
                    self._static_rag.retrieve,
                    embedding=embedding, query=english_query,
                    domain=domain, state=state,
                )
            except Exception:
                logger.exception("Static RAG pipeline failed")
                static_result = RAGResult(
                    chunks=[], abstained=True,
                    reason=AbstentionReason.PROVIDER_UNAVAILABLE, domain=domain,
                )
            static_result.metadata["retrieval_ms"] = (time.monotonic() - started) * 1000
            return static_result, abstain

        # Web-only mode (V2)
        if mode == "web":
            started = time.monotonic()
            try:
                web_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._web_rag.retrieve,
                        query=english_query, domain=domain,
                        state=state, classification=classification,
                    ),
                    timeout=self._settings.web_rag_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning("Web RAG timed out after %.1fs", self._settings.web_rag_timeout_s)
                web_result = RAGResult(
                    chunks=[], abstained=True,
                    reason=AbstentionReason.PROVIDER_UNAVAILABLE, domain=domain,
                )
            except Exception:
                logger.exception("Web RAG pipeline failed")
                web_result = RAGResult(
                    chunks=[], abstained=True,
                    reason=AbstentionReason.PROVIDER_UNAVAILABLE, domain=domain,
                )
            web_result.metadata["web_total_ms"] = (time.monotonic() - started) * 1000
            return abstain, web_result

        # Dual mode (V3) — both pipelines in parallel
        async def _static_with_timing() -> RAGResult:
            started = time.monotonic()
            result = await asyncio.to_thread(
                self._static_rag.retrieve,
                embedding=embedding, query=english_query,
                domain=domain, state=state,
            )
            result.metadata["retrieval_ms"] = (time.monotonic() - started) * 1000
            return result

        static_coro = _static_with_timing()
        web_coro = asyncio.to_thread(
            self._web_rag.retrieve,
            query=english_query, domain=domain,
            state=state, classification=classification,
        )

        # Keep Web RAG optional: static evidence must continue within a bounded budget.
        async def _web_with_timeout() -> RAGResult:
            started = time.monotonic()
            try:
                web_task = asyncio.create_task(web_coro)
                try:
                    result = await asyncio.wait_for(web_task, timeout=self._settings.web_rag_timeout_s)
                    result.metadata["web_total_ms"] = (time.monotonic() - started) * 1000
                    return result
                except asyncio.TimeoutError:
                    web_task.cancel()
                    await asyncio.gather(web_task, return_exceptions=True)
                    raise
            except asyncio.TimeoutError:
                logger.warning(
                    "Web RAG timed out after %.1fs — using static only",
                    self._settings.web_rag_timeout_s,
                )
                result = RAGResult(
                    chunks=[],
                    abstained=True,
                    reason=AbstentionReason.PROVIDER_UNAVAILABLE,
                    domain=domain,
                )
                result.metadata["web_total_ms"] = (time.monotonic() - started) * 1000
                return result

        results = await asyncio.gather(static_coro, _web_with_timeout(), return_exceptions=True)

        static_result: RAGResult
        web_result: RAGResult

        if isinstance(results[0], Exception):
            logger.exception("Static RAG pipeline failed: %s", results[0])
            static_result = RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.PROVIDER_UNAVAILABLE,
                domain=domain,
            )
        else:
            static_result = results[0]

        if isinstance(results[1], Exception):
            logger.exception("Web RAG pipeline failed: %s", results[1])
            web_result = RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.PROVIDER_UNAVAILABLE,
                domain=domain,
            )
        else:
            web_result = results[1]

        return static_result, web_result

    def _merge_evidence(
        self,
        static_chunks: list[EvidenceChunk],
        web_chunks: list[EvidenceChunk],
    ) -> list[EvidenceChunk]:
        """Merge evidence from both pipelines with cross-source ranking.

        Static chunks (official documents) get an authority boost.
        Weak web results cannot displace stronger static evidence.
        Deduplicates by chunk_id.
        """
        AUTHORITY_BOOST_STATIC = 0.05

        # Deduplicate by chunk_id, keeping the best score
        seen: dict[str, EvidenceChunk] = {}
        for chunk in static_chunks:
            scored = (chunk.dense_score or 0) + AUTHORITY_BOOST_STATIC
            existing = seen.get(chunk.chunk_id)
            if not existing or scored > ((existing.dense_score or 0) + AUTHORITY_BOOST_STATIC):
                seen[chunk.chunk_id] = chunk
        for chunk in web_chunks:
            existing = seen.get(chunk.chunk_id)
            if not existing or (chunk.dense_score or 0) > (existing.dense_score or 0):
                seen[chunk.chunk_id] = chunk

        # Sort by effective score descending
        merged = sorted(
            seen.values(),
            key=lambda c: -(c.dense_score or 0),
        )

        logger.info(
            "Merged evidence: %d static + %d web = %d total (after dedup+rank)",
            len(static_chunks), len(web_chunks), len(merged),
        )
        return merged

    def _auto_append_citations(
        self,
        answer: str,
        chunks: list[EvidenceChunk],
        force: bool = False,
    ) -> str:
        """Auto-append citation markers if omitted, and clean non-citation bracket markers."""
        # Clean loose non-citation brackets like [1], [Note], [Source]
        answer = re.sub(r"\[(?!\s*chunk:)(?!\s*web_)[A-Za-z0-9\s]+\]", "", answer)

        has_citation = bool(re.search(r"\[chunk:", answer))
        if (has_citation and not force) or not chunks:
            return answer

        seen: set[str] = set()
        citation_parts: list[str] = []
        for chunk in chunks[:3]:
            short_id = chunk.chunk_id[:8]
            if short_id not in seen:
                seen.add(short_id)
                citation_parts.append(f"[chunk:{short_id}]")

        if citation_parts:
            answer = answer.rstrip() + " " + " ".join(citation_parts)

        return answer

    def _build_citations(
        self,
        chunks: list[EvidenceChunk],
    ) -> list[dict]:
        """Build the citations list for the API response."""
        citations: list[dict] = []
        seen: set[str] = set()

        for chunk in chunks:
            short_id = chunk.chunk_id[:8]
            if short_id in seen:
                continue
            seen.add(short_id)

            citation: dict[str, Any] = {
                "chunk_id": short_id,
                "title": chunk.title,
                "source": chunk.source_type,
                "source_label": "Official Document" if chunk.source_type == "static" else "Web Source",
                "url": chunk.url,
                "content": chunk.content,
            }
            if chunk.page is not None:
                citation["page"] = chunk.page
            if chunk.section:
                citation["section"] = chunk.section
            source_file = chunk.metadata.get("source_file", "") if chunk.metadata else ""
            if source_file:
                citation["source_file"] = source_file

            citations.append(citation)

        return citations

    def _calculate_confidence(
        self,
        static_result: RAGResult,
        web_result: RAGResult,
        has_static: bool,
        has_web: bool,
        claim_verifications: list | None = None,
    ) -> tuple[float, ConfidenceBand]:
        """Compute claim-level confidence score and band from evidence.

        After claim verification, confidence is adjusted based on:
        - Number of unsupported claims (lower confidence)
        - Number of filtered claims (lower confidence)
        - Source quality (static + web dual-source boost)
        """
        # Static confidence from evidence gate band
        static_band = static_result.band
        static_conf = _BAND_TO_CONFIDENCE.get(
            getattr(static_band, "value", ""), 0.4
        )

        # Web confidence from evidence gate band
        web_band = web_result.band
        web_conf = _BAND_TO_CONFIDENCE.get(
            getattr(web_band, "value", ""), 0.4
        )

        if has_static and has_web:
            # Dual-source: average with a small boost
            base = (static_conf + web_conf) / 2.0
            confidence = min(base + 0.10, 1.0)
        elif has_static:
            confidence = static_conf
        elif has_web:
            confidence = web_conf
        else:
            confidence = 0.0

        # Adjust confidence based on claim verification results
        if claim_verifications is not None and len(claim_verifications) > 0:
            unsupported = sum(1 for v in claim_verifications if not v.is_supported)
            total_verified = len(claim_verifications)
            if total_verified > 0:
                # Each unsupported claim reduces confidence by a factor
                unsupported_ratio = unsupported / total_verified
                penalty = unsupported_ratio * 0.3  # up to 30% penalty
                confidence = max(confidence - penalty, 0.0)

        # Map to band
        if confidence >= 0.7:
            band = ConfidenceBand.HIGH
        elif confidence >= 0.5:
            band = ConfidenceBand.MEDIUM
        else:
            band = ConfidenceBand.LOW

        return round(confidence, 2), band

    def _abstain_response(
        self,
        lang: str,
        reason: AbstentionReason,
        domain: str,
        session_id: str,
    ) -> RAGResponse:
        """Build a standardized abstention response."""
        answer = get_abstain_text(lang)
        return RAGResponse(
            answer=answer,
            language=lang,
            domain=domain,
            confidence=0.0,
            confidence_level=ConfidenceBand.LOW,
            citations=[],
            abstained=True,
            speech_text=prepare_speech_text(answer),
            speech_segments=[],
            follow_up_question=None,
            mode="dual_rag",
            conversation_id=session_id,
        )
