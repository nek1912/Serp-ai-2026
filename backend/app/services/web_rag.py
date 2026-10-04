"""Web RAG service — 10-step evidence-only web RAG pipeline.

Architecture (eGovAssistant-style, evidence-only):

    User Query
         ↓
    Step 1: Domain scope gate
         ↓
    Step 2: Web discovery (Tavily/Firecrawl)
         ↓
    Step 3: BM25 ranking
         ↓
    Step 4: Gemini pre-ranking
         ↓
    Step 5: RRF fusion
         ↓
    Step 6: Gemini final reranking
         ↓
    Step 7: Relevance gate (threshold 60.0)
         ↓
    Step 8: Source verification
         ↓
    Step 9: Evidence threshold check
         ↓
    Step 10: Return EvidenceChunks (no answer generation)

IMPORTANT: This service returns evidence only.
Answer generation is handled by RAGOrchestrator (Task 5).
"""

from __future__ import annotations

import logging

from app.contracts import (
    AbstentionReason,
    ConfidenceBand,
    EvidenceChunk,
    RAGResult,
)
from app.evidence_gate import evidence_gate
from app.retrieval.gemini_reranker import GeminiReranker
from app.retrieval.rrf import reciprocal_rank_fusion
from app.security.source_verifier import SourceVerifier
from app.web_rag.bm25_ranker import WebBM25Ranker
from app.web_rag.query_classifier import QueryClassification
from app.web_rag.service import WebDiscoveryService

logger = logging.getLogger(__name__)

# Supported domains for web RAG (matching eGovAssistant scope)
SUPPORTED_DOMAINS = {
    "cooperative",
    "pacs",
    "schemes",
    "pmfby",
    "agriculture",
    "finlit",
    "grievance",
    "driving_licence",
}

# Minimum relevance score for evidence to pass the relevance gate
# (matching eGovAssistant DEFAULT_MIN_RELEVANCE_SCORE = 60.0)
DEFAULT_MIN_RELEVANCE_SCORE = 40.0


class WebRAGService:
    """10-step web RAG pipeline returning evidence only.

    Follows the eGovAssistant architecture but stops before answer
    generation. The RAGOrchestrator (Task 5) handles the final
    answer generation step.

    Usage:
        service = WebRAGService()
        result = service.retrieve(
            query="What is PMFBY?",
            domain="pmfby",
            state="gujarat",
        )
    """

    def __init__(
        self,
        bm25_top_k: int = 15,
        gemini_pre_top_k: int = 15,
        final_top_k: int = 8,
        rrf_k: int = 60,
        minimum_relevance_score: float = DEFAULT_MIN_RELEVANCE_SCORE,
        minimum_trust_score: float = 20.0,
    ):
        logger.info("Initializing WebRAGService")

        self.bm25_top_k = bm25_top_k
        self.gemini_pre_top_k = gemini_pre_top_k
        self.final_top_k = final_top_k
        self.rrf_k = rrf_k
        self.minimum_relevance_score = float(minimum_relevance_score)

        self.web_discovery = WebDiscoveryService()
        self.bm25 = WebBM25Ranker()
        self.reranker = GeminiReranker()
        self.source_verifier = SourceVerifier(
            minimum_trust_score=minimum_trust_score,
        )

        logger.info("WebRAGService initialized")

    def retrieve(
        self,
        query: str,
        domain: str | None = None,
        state: str | None = None,
        classification: QueryClassification | None = None,
        top_k: int | None = None,
    ) -> RAGResult:
        """Run the 10-step web RAG pipeline and return evidence.

        Args:
            query: User query text.
            domain: Expected domain filter.
            state: Expected state filter.
            classification: Pre-computed query classification (optional).
            top_k: Number of final evidence chunks to return.

        Returns:
            RAGResult with evidence chunks, abstention info, and confidence band.
        """
        query = str(query or "").strip()
        if not query:
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=domain or "",
            )

        if top_k is None:
            top_k = self.final_top_k

        effective_domain = domain or ""

        if classification is None:
            classification = self.web_discovery.classifier.classify(query, default_state=state)
        elif state and not classification.state:
            classification.state = state

        # ── Step 1: Domain scope gate ───────────────────────────────
        logger.info("[Step 1] Domain scope gate")

        # Use AnchorStore domain or classification domain if available
        effective_domain_for_gate = domain
        if classification is not None and classification.domain != "general":
            effective_domain_for_gate = classification.domain

        if not effective_domain_for_gate:
            effective_domain_for_gate = "general"

        # Allow "general" domain to proceed — the web discovery service
        # has its own domain classification that may identify a specific
        # domain from the query. Only block truly empty/unknown domains.
        if effective_domain_for_gate not in SUPPORTED_DOMAINS and effective_domain_for_gate != "general":
            logger.info(
                "Domain scope gate: UNSUPPORTED DOMAIN (%s). Abstaining.",
                effective_domain_for_gate,
            )
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.DOMAIN_MISMATCH,
                band=ConfidenceBand.LOW,
                domain=effective_domain_for_gate,
                metadata={"step": "domain_scope_gate"},
            )
        elif effective_domain_for_gate == "general":
            logger.info(
                "Domain scope gate: general domain — proceeding with web discovery (internal classifier may refine)."
            )

        # ── Step 2: Web discovery ──────────────────────────────────
        logger.info("[Step 2] Web discovery")

        try:
            discovery = self.web_discovery.discover(
                query=query,
                classification=classification,
            )
        except Exception:
            logger.exception("Web discovery failed")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.PROVIDER_UNAVAILABLE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "web_discovery", "error": "provider_failure"},
            )

        discovered_results = discovery.get("results", [])
        classification_data = discovery.get("classification", {})

        logger.info(
            "Discovery: domain=%s jurisdiction=%s state=%s results=%d",
            classification_data.get("domain"),
            classification_data.get("jurisdiction"),
            classification_data.get("state"),
            len(discovered_results),
        )

        if not discovered_results:
            logger.info("No discovered results. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "web_discovery", "discovered": 0},
            )

        # ── Step 3: BM25 ranking ──────────────────────────────────
        logger.info("[Step 3] BM25 ranking")

        try:
            bm25_results = self.bm25.rank_candidates(
                query=query,
                candidates=discovered_results,
                top_k=self.bm25_top_k,
            )
        except Exception:
            logger.exception("BM25 ranking failed")
            bm25_results = discovered_results[: self.bm25_top_k]

        logger.info("BM25 candidates: %d", len(bm25_results))

        if not bm25_results:
            logger.info("BM25 produced no results. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "bm25_ranking", "bm25_count": 0},
            )

        # ── Step 4: Fast candidate pre-ranking ─────────────────────────────
        logger.info("[Step 4] Fast candidate pre-ranking")

        gemini_pre_results = self.reranker._passthrough_pre_rank(
            query=query,
            candidates=discovered_results,
            top_k=self.gemini_pre_top_k,
        )

        if not gemini_pre_results:
            logger.info("Gemini pre-ranking produced no results. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "gemini_pre_rank", "pre_rank_count": 0},
            )

        # ── Step 5: RRF fusion ─────────────────────────────────────
        logger.info("[Step 5] RRF fusion")

        try:
            fused_results = reciprocal_rank_fusion(
                result_lists=[bm25_results, gemini_pre_results],
                k=self.rrf_k,
                top_k=None,
            )
        except Exception:
            logger.exception("RRF fusion failed")
            fused_results = gemini_pre_results

        logger.info("RRF fused candidates: %d", len(fused_results))

        if not fused_results:
            logger.info("RRF produced no fused results. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "rrf_fusion", "fused_count": 0},
            )

        # ── Step 6: Gemini final reranking ─────────────────────────
        logger.info("[Step 6] Gemini final reranking")

        try:
            final_results = self.reranker.final_rerank(
                query=query,
                candidates=fused_results,
                top_k=top_k,
                classification=classification_data,
            )
        except Exception:
            logger.exception("Gemini final reranking failed")
            final_results = fused_results[:top_k]

        # Enrich with classification metadata (only when present)
        for result in final_results:
            if classification_data.get("domain"):
                result["query_domain"] = classification_data["domain"]
            if classification_data.get("jurisdiction"):
                result["jurisdiction"] = classification_data["jurisdiction"]
            if classification_data.get("state"):
                result["state"] = classification_data["state"]
            if classification_data.get("confidence"):
                result["classification_confidence"] = classification_data["confidence"]

        logger.info("Final evidence chunks: %d", len(final_results))

        if not final_results:
            logger.info("Final reranking produced no results. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={"step": "gemini_final_rerank", "final_count": 0},
            )

        # ── Step 7: Relevance gate ─────────────────────────────────
        logger.info("[Step 7] Relevance gate (threshold=%.1f)", self.minimum_relevance_score)

        relevance_result = self._check_evidence_relevance(final_results)

        logger.info(
            "Relevance: status=%s top_score=%.1f relevant_count=%d",
            relevance_result.get("status"),
            relevance_result.get("top_score", 0.0),
            relevance_result.get("relevant_count", 0),
        )

        if not relevance_result.get("relevant", False):
            logger.info("Relevance gate FAILED. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.BELOW_TOP1_THRESHOLD,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={
                    "step": "relevance_gate",
                    "relevance_status": relevance_result.get("status"),
                    "top_score": relevance_result.get("top_score", 0.0),
                },
            )

        # ── Step 8: Source verification ────────────────────────────
        logger.info("[Step 8] Source verification")

        try:
            verification_result = self.source_verifier.verify_and_filter(final_results)
        except Exception:
            logger.exception("Source verification failed")
            verification_result = {
                "verified_sources": final_results,
                "accepted_sources": final_results,
                "rejected_sources": [],
                "summary": {},
            }

        accepted_sources = verification_result.get("accepted_sources", [])
        rejected_sources = verification_result.get("rejected_sources", [])
        verification_summary = verification_result.get("summary", {})

        logger.info(
            "Verification: accepted=%d rejected=%d",
            len(accepted_sources),
            len(rejected_sources),
        )

        # ── Step 9: Evidence threshold check ───────────────────────
        logger.info("[Step 9] Evidence threshold check")

        if not accepted_sources:
            logger.info("No accepted sources after verification. Abstaining.")
            return RAGResult(
                chunks=[],
                abstained=True,
                reason=AbstentionReason.INSUFFICIENT_EVIDENCE,
                band=ConfidenceBand.LOW,
                domain=effective_domain,
                metadata={
                    "step": "evidence_threshold",
                    "verification_summary": verification_summary,
                },
            )

        # ── Step 10: Return EvidenceChunks ─────────────────────────
        logger.info("[Step 10] Converting to EvidenceChunks")

        evidence_chunks = [
            self._to_evidence_chunk(source, classification_data)
            for source in accepted_sources
        ]

        # Apply unified evidence gate (min_chunks=1 for web evidence —
        # a single authoritative web source can be sufficient)
        try:
            gate_abstained, gate_reason, gate_band = evidence_gate(
                evidence_chunks,
                expected_domain=classification_data.get("domain", effective_domain),
                expected_state=state or classification_data.get("state"),
                min_chunks=1,
            )
        except Exception:
            logger.exception("Evidence gate failed")
            gate_abstained, gate_reason, gate_band = (
                False,
                None,
                ConfidenceBand.MEDIUM,
            )

        logger.info(
            "WebRAGService result: chunks=%d abstained=%s reason=%s band=%s",
            len(evidence_chunks),
            gate_abstained,
            gate_reason,
            gate_band,
        )

        return RAGResult(
            chunks=evidence_chunks,
            abstained=gate_abstained,
            reason=gate_reason,
            band=gate_band,
            domain=classification_data.get("domain", effective_domain),
            metadata={
                "discovery_stage": discovery.get("discovery_stage"),
                "verification_summary": verification_summary,
                "relevance": relevance_result,
            },
        )

    def _check_evidence_relevance(
        self,
        results: list[dict],
    ) -> dict:
        """Check whether retrieved evidence is sufficiently relevant.

        Uses the eGovAssistant relevance scale:
            100 = directly answers
            80-99 = highly relevant
            60-79 = useful supporting evidence
            40-59 = somewhat relevant
            20-39 = weakly relevant
            0-19 = irrelevant
        """
        if not results:
            return {
                "relevant": False,
                "status": "no_final_evidence",
                "top_score": 0.0,
                "relevant_count": 0,
                "scores": [],
            }

        scores: list[float] = []
        explicitly_inapplicable: list[dict] = []

        for result in results:
            if not isinstance(result, dict):
                continue

            applicable = result.get("rerank_applicable")
            if (
                applicable is False
                or str(applicable).strip().lower()
                in {"false", "no", "not_applicable", "irrelevant"}
            ):
                explicitly_inapplicable.append(result)

            raw_score = result.get("rerank_score")
            if raw_score is None:
                raw_score = result.get("gemini_score")
            if raw_score is None:
                raw_score = result.get("relevance_score")

            try:
                score = float(raw_score if raw_score is not None else 0.0)
            except (TypeError, ValueError):
                score = 0.0

            scores.append(score)

        if not scores:
            return {
                "relevant": False,
                "status": "no_relevance_scores",
                "top_score": 0.0,
                "relevant_count": 0,
                "scores": [],
            }

        top_score = max(scores)
        relevant_scores = [
            s for s in scores if s >= self.minimum_relevance_score
        ]

        if not relevant_scores:
            return {
                "relevant": False,
                "status": "below_relevance_threshold",
                "top_score": top_score,
                "relevant_count": 0,
                "scores": scores,
            }

        # All candidates inapplicable
        if len(explicitly_inapplicable) == len(results):
            return {
                "relevant": False,
                "status": "all_candidates_inapplicable",
                "top_score": top_score,
                "relevant_count": 0,
                "scores": scores,
            }

        return {
            "relevant": True,
            "status": "relevant",
            "top_score": top_score,
            "relevant_count": len(relevant_scores),
            "scores": scores,
        }

    @staticmethod
    def _to_evidence_chunk(
        source: dict,
        classification_data: dict,
    ) -> EvidenceChunk:
        """Convert a verified source dict to an EvidenceChunk contract."""
        return EvidenceChunk(
            chunk_id=source.get("chunk_id", ""),
            content=source.get("text", source.get("content", "")),
            source_type="web",
            title=source.get("title", source.get("web_title", "")),
            url=source.get("source_url", source.get("url", "")),
            page=source.get("page"),
            section=source.get("section", source.get("section_title", "")),
            domain=source.get("query_domain", classification_data.get("domain", "")),
            jurisdiction=source.get("jurisdiction") or classification_data.get("jurisdiction") or "",
            state=source.get("state", classification_data.get("state")),
            dense_score=source.get("rerank_score", source.get("gemini_score")),
            bm25_score=source.get("bm25_score"),
            rerank_score=source.get("rerank_score"),
            trust_score=source.get("trust_score"),
            metadata={
                "document_id": source.get("document_id"),
                "source_type": source.get("source_type", "url"),
                "official": source.get("official", False),
                "trusted_secondary": source.get("trusted_secondary", False),
                "verification_status": source.get("verification_status"),
                "authority_tier": source.get("authority_tier"),
                "authority_label": source.get("authority_label"),
                "discovery_stage": source.get("discovery_stage"),
                "rrf_score": source.get("rrf_score"),
                "applicability_reason": source.get("applicability_reason"),
            },
        )
