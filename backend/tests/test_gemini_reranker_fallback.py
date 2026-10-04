"""Regression tests for bounded Gemini reranking and fallback provenance."""

from __future__ import annotations

from unittest.mock import Mock, patch

from app.retrieval.gemini_reranker import GeminiReranker


CANDIDATES = [
    {"chunk_id": "web_a", "text": "PMFBY evidence", "rrf_score": 0.03},
    {"chunk_id": "web_b", "text": "Other evidence", "rrf_score": 0.02},
]


def _reranker() -> GeminiReranker:
    reranker = GeminiReranker.__new__(GeminiReranker)
    reranker._enabled = True
    reranker.client = Mock()
    reranker.model = "test-model"
    return reranker


def _gemini_result() -> dict:
    return {
        "ranked_chunks": [
            {
                "chunk_id": "web_a",
                "relevance_score": 90,
                "applicable": True,
                "applicability_reason": "direct",
            }
        ],
        "merge_groups": [],
    }


def test_gemini_success_is_authoritative() -> None:
    reranker = _reranker()

    with patch.object(reranker, "_try_gemini", return_value=_gemini_result()) as gemini, patch.object(
        reranker, "_try_jina_fallback"
    ) as jina:
        result = reranker._call_gemini("query", CANDIDATES, {})

    assert result["reranker_used"] == "gemini"
    assert "reranker_fallback_reason" not in result
    gemini.assert_called_once()
    jina.assert_not_called()


def test_gemini_503_uses_jina_and_records_reason() -> None:
    reranker = _reranker()
    jina_result = {
        "ranked_chunks": [],
        "merge_groups": [],
    }

    with patch.object(reranker, "_try_gemini", side_effect=RuntimeError("503 Service Unavailable")), patch.object(
        reranker, "_try_jina_fallback", return_value=jina_result
    ) as jina:
        result = reranker._call_gemini("query", CANDIDATES, {})

    assert result["reranker_used"] == "jina"
    assert result["reranker_fallback_reason"] == "gemini_503"
    jina.assert_called_once()


def test_gemini_timeout_uses_jina() -> None:
    reranker = _reranker()
    with patch.object(reranker, "_try_gemini", side_effect=TimeoutError("timed out")), patch.object(
        reranker,
        "_try_jina_fallback",
        return_value={"ranked_chunks": [], "merge_groups": []},
    ) as jina:
        result = reranker._call_gemini("query", CANDIDATES, {})

    assert result["reranker_used"] == "jina"
    assert result["reranker_fallback_reason"] == "gemini_timeout"
    jina.assert_called_once()
