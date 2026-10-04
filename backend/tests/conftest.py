import pytest

from app.config import get_settings


class _FakeStore:
    @staticmethod
    def classify(_text: str, _embedding: list[float]) -> tuple[str, float]:
        return "pmfby", 1.0  # classifier is unit-tested in Task 7


@pytest.fixture(autouse=True)
def env_and_route_stubs(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("SUPABASE_URL", "http://testsupa")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setenv("JINA_API_KEY", "")  # force Gemini provider in tests
    get_settings.cache_clear()
    from app.providers.embeddings import get_embedding_provider
    get_embedding_provider.cache_clear()
    import app.routes.chat as chat_route

    # Clear module-level per-query caches between tests so reused question
    # text cannot bypass the mocked embedding/classification provider.
    chat_route._cached_embedding.cache_clear()
    chat_route._cached_classification.cache_clear()

    monkeypatch.setattr(chat_route, "get_anchor_store", lambda: _FakeStore())
    monkeypatch.setattr(chat_route, "get_state", lambda _sid: None)
    monkeypatch.setattr(chat_route, "touch_session", lambda *_a, **_k: None)
    yield
    get_settings.cache_clear()
    get_embedding_provider.cache_clear()
    chat_route._cached_embedding.cache_clear()
    chat_route._cached_classification.cache_clear()
