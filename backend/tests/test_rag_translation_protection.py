"""RAG back-translation protection (P0): digit-free placeholders + URL safety.

Sarvam translates with numerals_format=native, which localizes ASCII digits
inside placeholder tokens so they can never be restored. Placeholders must
therefore be alphabetic-only, and raw portal URLs must survive translation.
"""
from unittest.mock import patch

from app.config import Settings
from app.routes import chat as chat_route


def _settings() -> Settings:
    return Settings(
        groq_api_key="test", gemini_api_key="test",
        supabase_url="http://test", supabase_service_key="test",
    )


def test_alpha_index_is_digit_free_and_unique():
    seen = set()
    for i in range(5000):
        token = chat_route._alpha_index(i)
        assert token and all("A" <= c <= "Z" for c in token), token
        assert token not in seen
        seen.add(token)
    assert chat_route._alpha_index(0) == "A"
    assert chat_route._alpha_index(25) == "Z"
    assert chat_route._alpha_index(26) == "AA"


def test_back_translation_restores_citations_and_urls():
    text = "Apply at https://pmfby.gov.in/portal for details [chunk:abcdefgh]."
    with patch.object(chat_route, "SarvamTranslator") as mock_cls:
        inst = mock_cls.return_value
        inst.configured = True
        # Simulate a translation that changes the prose (append marker).
        inst.translate.side_effect = lambda t, **kw: t + "!"
        out = chat_route._translate_from_english(text, "gu", _settings())
    assert out == text + "!"
    assert "TRANSLATIONCITATION" not in out
    assert "RAGURL" not in out
    assert "https://pmfby.gov.in/portal" in out
    assert "[chunk:abcdefgh]" in out


def test_back_translation_falls_back_to_original_without_leak():
    text = "See https://example.gov.in/x [chunk:abcdefgh] for help."
    with patch.object(chat_route, "SarvamTranslator") as mock_cls:
        mock_cls.return_value.configured = False
        with patch.object(chat_route, "AzureTranslator") as azure_cls:
            azure_cls.return_value.translate.side_effect = RuntimeError("down")
            out = chat_route._translate_from_english(text, "gu", _settings())
    assert out == text
    assert "TRANSLATIONCITATION" not in out
    assert "RAGURL" not in out
