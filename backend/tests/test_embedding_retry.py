from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.providers.embeddings import GeminiEmbeddingProvider
from app.config import Settings


def _make_provider() -> GeminiEmbeddingProvider:
    return GeminiEmbeddingProvider(Settings(
        groq_api_key="test", gemini_api_key="test",
        supabase_url="http://test", supabase_service_key="test",
    ))


def test_retry_on_rate_limit():
    """Verify retry logic handles 429 errors."""
    provider = _make_provider()
    call_count = [0]

    def mock_post(self, url, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            response = MagicMock()
            response.status_code = 429
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "rate limited", request=MagicMock(), response=response
            )
            return response
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"embedding": {"values": [0.1] * 768}}
        return response

    with patch("httpx.Client.post", mock_post):
        result = provider.embed_texts(["test text"])
        assert len(result) == 1
        assert len(result[0]) == 768
        assert call_count[0] == 3


def test_retry_on_server_error():
    """Verify retry logic handles 500 errors."""
    provider = _make_provider()
    call_count = [0]

    def mock_post(self, url, **kwargs):
        call_count[0] += 1
        if call_count[0] < 2:
            response = MagicMock()
            response.status_code = 500
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "server error", request=MagicMock(), response=response
            )
            return response
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"embedding": {"values": [0.1] * 768}}
        return response

    with patch("httpx.Client.post", mock_post):
        result = provider.embed_texts(["test text"])
        assert len(result) == 1
        assert call_count[0] == 2


def test_non_retryable_error_raises_immediately():
    """400/401 errors should not be retried."""
    provider = _make_provider()
    call_count = [0]

    def mock_post(self, url, **kwargs):
        call_count[0] += 1
        response = MagicMock()
        response.status_code = 400
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "bad request", request=MagicMock(), response=response
        )
        return response

    with patch("httpx.Client.post", mock_post):
        with pytest.raises(httpx.HTTPStatusError):
            provider.embed_texts(["test text"])
        assert call_count[0] == 1


def test_retry_exhausted_raises_last_error():
    """After max retries, the last exception is raised."""
    provider = _make_provider()

    def mock_post(self, url, **kwargs):
        response = MagicMock()
        response.status_code = 429
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=response
        )
        return response

    with patch("httpx.Client.post", mock_post):
        with pytest.raises(httpx.HTTPStatusError):
            provider.embed_texts(["test text"])


def test_retry_on_timeout():
    """Verify retry logic handles connection timeouts."""
    provider = _make_provider()
    call_count = [0]

    def mock_post(self, url, **kwargs):
        call_count[0] += 1
        if call_count[0] < 2:
            raise httpx.TimeoutException("timeout")
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"embedding": {"values": [0.1] * 768}}
        return response

    with patch("httpx.Client.post", mock_post):
        result = provider.embed_texts(["test text"])
        assert len(result) == 1
        assert call_count[0] == 2


def test_success_on_first_attempt():
    """No retries when the first call succeeds."""
    provider = _make_provider()
    call_count = [0]

    def mock_post(self, url, **kwargs):
        call_count[0] += 1
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"embedding": {"values": [0.1] * 768}}
        return response

    with patch("httpx.Client.post", mock_post):
        result = provider.embed_texts(["test text"])
        assert len(result) == 1
        assert call_count[0] == 1
