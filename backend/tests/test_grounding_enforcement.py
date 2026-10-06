"""P0-2 regression tests: grounding must be enforced, not advisory.

Live failure: grounding detected 3 unsupported claims (['may', 'may', 'PACS'])
but the orchestrator still returned confidence=0.90/high/abstained=False.

Covers the acceptance criteria:
1. Fully supported answer -> returned normally.
2. Genuinely unsupported factual claim -> removed/repaired or safe abstention.
3. Irrelevant unsupported entity (PACS) -> cannot remain without evidence support.
4. Legitimate "may" supported by evidence flow -> no false abstention.
5. Citation failure -> repair works or safe failure.
6. Grounding failure -> no HIGH-confidence answer with unsupported claims left.
7. Existing safe abstention behavior intact.
8. No regression to Gujarati final language handling.
"""
from __future__ import annotations

from unittest.mock import patch

from app.answer_grounding import (
    UnsupportedClaim,
    GroundingResult,
    verify_answer_grounding,
)
from app.citation_verifier import VerificationResult
from app.config import Settings
from app.contracts import (
    AbstentionReason,
    ConfidenceBand,
    EvidenceChunk,
    RAGResult,
)
from app.contracts import EvidenceAssessment, EvidenceSufficiency, SourceRole
from app.contracts import (
    DynamicEvidence,
    EvidenceBundle,
    QueryRequirements,
    StaticEvidence,
)
from app.services.rag_orchestrator import RAGOrchestrator
from app.web_rag.query_classifier import QueryClassification


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    defaults = {
        "groq_api_key": "test-groq-key",
        "gemini_api_key": "test-gemini-key",
        "jina_api_key": "test-jina-key",
        "supabase_url": "https://test.supabase.co",
        "supabase_service_key": "test-key",
        "reranker_enabled": False,
        "sarvam_api_key": "",
        "sarvam_api_key_2": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_evidence_chunk(
    chunk_id: str = "abc12345def67890",
    content: str = "PMFBY provides crop insurance to farmers.",
    source_type: str = "web",
    title: str = "PMFBY Overview",
    url: str = "https://pmfby.gov.in",
    domain: str = "pmfby",
    dense_score: float = 0.75,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        content=content,
        source_type=source_type,
        title=title,
        url=url,
        page=1,
        section="Overview",
        domain=domain,
        jurisdiction="central",
        state=None,
        dense_score=dense_score,
    )


def _make_rag_result(
    chunks: list[EvidenceChunk] | None = None,
    abstained: bool = False,
    reason: AbstentionReason | None = None,
    band: ConfidenceBand | None = None,
    domain: str = "pmfby",
) -> RAGResult:
    return RAGResult(
        chunks=chunks or [],
        abstained=abstained,
        reason=reason,
        band=band,
        domain=domain,
    )


def _make_classification() -> QueryClassification:
    return QueryClassification(
        domain="pmfby",
        jurisdiction="central",
        state=None,
        intent="INFORMATIONAL",
        confidence=0.85,
    )


def _make_bundle(web_chunks: list[EvidenceChunk]) -> EvidenceBundle:
    return EvidenceBundle(
        static=StaticEvidence(available=False, chunks=[], summary="none"),
        dynamic=DynamicEvidence(available=True, chunks=web_chunks),
        query_requirements=QueryRequirements(
            temporal_scope="current",
            geographic_scope="state",
            required_specificity="state",
            requires_dynamic=True,
        ),
        query="test query",
    )


def _assessment() -> EvidenceAssessment:
    return EvidenceAssessment(
        source_role=SourceRole.WEB_PRIMARY,
        sufficiency=EvidenceSufficiency.SUFFICIENT,
        static_quality="low",
        web_quality="medium",
        assessment_text="test",
    )


async def _run_orchestrator(
    grounded_answer_text: str,
    web_chunks: list[EvidenceChunk],
    lang: str = "en",
    verify_side_effect=None,
    citations_side_effect=None,
):
    """Run the orchestrator with real grounding/citations unless overridden."""
    orch = RAGOrchestrator(_make_settings())
    static_result = _make_rag_result(
        abstained=True, reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
        band=ConfidenceBand.LOW,
    )
    web_result = _make_rag_result(
        chunks=web_chunks, abstained=False, band=ConfidenceBand.MEDIUM,
    )
    grounding_patcher = (
        patch(
            "app.services.rag_orchestrator.verify_answer_grounding",
            side_effect=verify_side_effect,
        )
        if verify_side_effect is not None
        else _nullcontext()
    )
    citations_patcher = (
        patch(
            "app.services.rag_orchestrator.verify_citations",
            side_effect=citations_side_effect,
        )
        if citations_side_effect is not None
        else _nullcontext()
    )
    with patch.object(orch._static_rag, "retrieve", return_value=static_result), \
        patch.object(orch._web_rag, "retrieve", return_value=web_result), \
        patch.object(
            orch._evidence_controller, "build_bundle",
            return_value=_make_bundle(web_chunks),
        ), \
        patch.object(
            orch._evidence_controller, "assess_evidence", return_value=_assessment(),
        ), \
        patch.object(
            orch._evidence_controller, "build_curated_prompt",
            return_value=("system", "user prompt"),
        ), \
        patch(
            "app.services.rag_orchestrator.grounded_answer",
            return_value=grounded_answer_text,
        ), \
        grounding_patcher, citations_patcher:
        return await orch.run(
            query="test query",
            english_query="test query",
            embedding=[0.1] * 768,
            domain="pmfby",
            state="gujarat",
            classification=_make_classification(),
            history=None,
            lang=lang,
            session_id="sess-grounding",
        )


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# 1. Fully supported answer -> returned normally
# ---------------------------------------------------------------------------


class TestSupportedAnswer:
    async def test_supported_answer_returned_normally(self):
        chunks = [_make_evidence_chunk(
            content="PMFBY premium is 2% for kharif crops.",
        )]
        response = await _run_orchestrator(
            "PMFBY premium is 2% for kharif crops. [chunk:abc12345]",
            chunks,
        )
        assert response.abstained is False
        assert "2%" in response.answer
        assert response.confidence > 0.0


# ---------------------------------------------------------------------------
# 2. Genuinely unsupported factual claim -> repaired or safe abstention
# ---------------------------------------------------------------------------


class TestUnsupportedNumber:
    async def test_unsupported_number_not_silently_kept(self):
        chunks = [_make_evidence_chunk(
            content="The premium is 2% of the sum insured.",
        )]
        response = await _run_orchestrator(
            "The premium is 12% per annum. "
            "The premium is 2% of the sum insured. [chunk:abc12345]",
            chunks,
        )
        # Either the bad sentence was repaired away or we abstained —
        # but 12% must never appear in a non-abstained answer.
        if response.abstained:
            assert response.confidence == 0.0
        else:
            assert "12%" not in response.answer
            assert "2%" in response.answer


# ---------------------------------------------------------------------------
# 3. Irrelevant unsupported entity (PACS) cannot remain without support
# ---------------------------------------------------------------------------


class TestUnsupportedPACSEntity:
    async def test_pacs_removed_when_evidence_does_not_support_it(self):
        chunks = [_make_evidence_chunk(
            content="Register on the official Gujarat farmer registry portal "
                    "with Aadhaar and land records.",
        )]
        response = await _run_orchestrator(
            "Register on the official Gujarat farmer registry portal. "
            "Visit the PACS office for help. [chunk:abc12345]",
            chunks,
        )
        assert response.abstained is False
        assert "PACS" not in response.answer
        assert "registry portal" in response.answer

    async def test_pacs_kept_when_evidence_supports_it(self):
        chunks = [_make_evidence_chunk(
            content="Visit the PACS office with your membership passbook. "
                    "The PACS secretary will help you apply.",
        )]
        response = await _run_orchestrator(
            "Visit the PACS office with your membership passbook. [chunk:abc12345]",
            chunks,
        )
        assert response.abstained is False
        assert "PACS" in response.answer


# ---------------------------------------------------------------------------
# 4. Legitimate "may" must not cause false abstention (+ extractor unit tests)
# ---------------------------------------------------------------------------


class TestMayFalsePositive:
    def test_bare_may_is_not_a_date_claim(self):
        chunk = _make_evidence_chunk(content="The portal accepts online applications.")
        result = verify_answer_grounding(
            "Farmers may apply online on the portal.",
            [chunk],
        )
        assert result.has_unsupported_claims is False

    def test_real_date_mismatch_still_flagged(self):
        chunk = _make_evidence_chunk(content="The deadline is 31 March 2025.")
        result = verify_answer_grounding(
            "The deadline is 15 April 2025.",
            [chunk],
        )
        assert result.has_unsupported_claims is True

    def test_matching_date_not_flagged(self):
        chunk = _make_evidence_chunk(content="The deadline is 31 March 2025.")
        result = verify_answer_grounding(
            "The deadline is 31 March 2025.",
            [chunk],
        )
        assert result.has_unsupported_claims is False

    async def test_may_sentence_does_not_abstain(self):
        chunks = [_make_evidence_chunk(
            content="The portal accepts online applications.",
        )]
        response = await _run_orchestrator(
            "Farmers may apply online on the portal. [chunk:abc12345]",
            chunks,
        )
        assert response.abstained is False
        assert "may" in response.answer


# ---------------------------------------------------------------------------
# 5. Citation failure -> repair works or safe failure
# ---------------------------------------------------------------------------


class TestCitationFailure:
    async def test_persistent_citation_failure_abstains(self):
        chunks = [_make_evidence_chunk()]
        response = await _run_orchestrator(
            "PMFBY answer. [chunk:ffffffff]",
            chunks,
            citations_side_effect=[
                VerificationResult(
                    is_valid=False,
                    invalid_prefixes=["ffffffff"],
                    reason=AbstentionReason.CITATION_FAILURE,
                ),
                VerificationResult(
                    is_valid=False,
                    invalid_prefixes=["ffffffff"],
                    reason=AbstentionReason.CITATION_FAILURE,
                ),
            ],
        )
        assert response.abstained is True
        assert response.confidence == 0.0
        assert response.citations == []

    async def test_repaired_citations_proceed(self):
        """Existing repair path: invalid -> valid still returns an answer."""
        chunks = [_make_evidence_chunk()]
        response = await _run_orchestrator(
            "PMFBY answer. [chunk:ffffffff]",
            chunks,
            citations_side_effect=[
                VerificationResult(
                    is_valid=False,
                    invalid_prefixes=["ffffffff"],
                    reason=AbstentionReason.CITATION_FAILURE,
                ),
                VerificationResult(is_valid=True),
            ],
        )
        assert response.abstained is False


# ---------------------------------------------------------------------------
# 6. Grounding failure can never yield HIGH confidence with claims remaining
# ---------------------------------------------------------------------------


class TestGroundingEnforcement:
    def _persistent_failure(self, *args, **kwargs):
        return GroundingResult(
            has_unsupported_claims=True,
            unsupported_claims=[
                UnsupportedClaim(
                    claim_text="12%",
                    claim_type="number",
                    reason="Number '12%' not found in evidence",
                )
            ],
        )

    async def test_persistent_grounding_failure_abstains(self):
        chunks = [_make_evidence_chunk(
            content="The premium is 2% of the sum insured.",
        )]
        response = await _run_orchestrator(
            "The premium is 12% per annum. [chunk:abc12345]",
            chunks,
            verify_side_effect=self._persistent_failure,
        )
        assert response.abstained is True
        assert response.confidence == 0.0
        assert response.confidence_level == ConfidenceBand.LOW
        assert response.citations == []

    async def test_repaired_answer_is_rechecked(self):
        """A repair that cleans the answer returns normally (not abstained)."""
        chunks = [_make_evidence_chunk(
            content="Register on the official farmer registry portal.",
        )]
        response = await _run_orchestrator(
            "Register on the official farmer registry portal. "
            "Visit the PACS office for help. [chunk:abc12345]",
            chunks,
        )
        assert response.abstained is False
        assert "PACS" not in response.answer


# ---------------------------------------------------------------------------
# 7. Existing safe abstention behavior remains intact
# ---------------------------------------------------------------------------


class TestAbstentionIntact:
    async def test_both_pipelines_abstained_still_abstains(self):
        orch = RAGOrchestrator(_make_settings())
        static_result = _make_rag_result(
            abstained=True, reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
            band=ConfidenceBand.LOW,
        )
        web_result = _make_rag_result(
            abstained=True, reason=AbstentionReason.NO_ELIGIBLE_SOURCE,
            band=ConfidenceBand.LOW,
        )
        with patch.object(orch._static_rag, "retrieve", return_value=static_result), \
            patch.object(orch._web_rag, "retrieve", return_value=web_result):
            response = await orch.run(
                query="What is the weather?",
                english_query="What is the weather?",
                embedding=[0.1] * 768,
                domain="general",
                state=None,
                classification=None,
                history=None,
                lang="en",
                session_id="sess-abstain",
            )
        assert response.abstained is True
        assert response.confidence == 0.0
        assert response.citations == []


# ---------------------------------------------------------------------------
# 8. Gujarati language handling preserved
# ---------------------------------------------------------------------------


class TestGujaratiPreserved:
    async def test_gujarati_success_keeps_language(self):
        chunks = [_make_evidence_chunk(
            content="PMFBY premium is 2% for kharif crops.",
        )]
        response = await _run_orchestrator(
            "PMFBY premium is 2% for kharif crops. [chunk:abc12345]",
            chunks,
            lang="gu",
        )
        assert response.abstained is False
        assert response.language == "gu"

    async def test_gujarati_grounding_failure_abstains_in_gujarati(self):
        chunks = [_make_evidence_chunk(
            content="The premium is 2% of the sum insured.",
        )]
        response = await _run_orchestrator(
            "The premium is 12% per annum. [chunk:abc12345]",
            chunks,
            lang="gu",
            verify_side_effect=TestGroundingEnforcement()._persistent_failure,
        )
        assert response.abstained is True
        assert response.language == "gu"
        assert response.answer
