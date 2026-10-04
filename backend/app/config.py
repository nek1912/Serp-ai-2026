from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    groq_api_key: str
    gemini_api_key: str = ""
    jina_api_key: str = ""
    jina_api_key_2: str = ""
    supabase_url: str
    supabase_service_key: str
    allowed_origins: str = "http://localhost:3000"
    selected_state: str | None = "gujarat"
    # Model IDs loaded directly from environment (.env)
    groq_model: str = ""
    groq_fallback_model: str = ""
    gemini_model: str = ""
    gemini_fallback_model: str = ""
    embed_model: str = ""
    embedding_model: str = "jina-embeddings-v3"
    jina_embed_model: str = ""
    reranker_model: str = ""
    reranker_enabled: bool = False
    retrieval_strategy: str = "dense"  # dense | hybrid | hybrid_reranked

    @property
    def groq_model_list(self) -> list[str]:
        models = []
        if self.groq_model:
            models.append(self.groq_model)
        if self.groq_fallback_model:
            models.extend([m.strip() for m in self.groq_fallback_model.split(",") if m.strip()])
        return list(dict.fromkeys(models))

    @property
    def gemini_model_list(self) -> list[str]:
        models = []
        if self.gemini_model:
            models.append(self.gemini_model)
        if self.gemini_fallback_model:
            models.extend([m.strip() for m in self.gemini_fallback_model.split(",") if m.strip()])
        return list(dict.fromkeys(models))
    # Azure Speech Services (PHASE 13 — voice I/O)
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    # Azure Translator (Phase 10 — multilingual query normalization)
    azure_translator_key: str = ""
    azure_translator_region: str = ""
    azure_translator_endpoint: str = ""
    # TTS voice names per language — configurable, not hardcoded
    # Voice IDs are the documented Azure neural voices for each locale; verify
    # against the live Azure Speech voice list before relying on a specific ID.
    azure_tts_voices: str = (
        "en:en-IN-NeerjaNeural,hi:hi-IN-SwaraNeural,gu:gu-IN-DhwaniNeural,"
        "mr:mr-IN-AarohiNeural,bn:bn-IN-TanishaaNeural"
    )
    # BCP-47 recognition/synthesis locale per language — configurable, not hardcoded
    azure_speech_locales: str = "en:en-IN,hi:hi-IN,gu:gu-IN,mr:mr-IN,bn:bn-IN"
    # Sarvam AI (STT, TTS, Translation — Indian languages)
    sarvam_api_key: str = ""
    sarvam_api_key_2: str = ""
    sarvam_chat_model: str = "sarvam-105b-conversations"
    sarvam_chat_url: str = "https://api.sarvam.ai/v1/chat/completions"

    # Groq LLM (primary) — multiple keys for rotation
    groq_api_key_1: str = ""
    groq_api_key_2: str = ""

    # Web search providers
    tavily_api_key_1: str = ""
    tavily_api_key_2: str = ""
    firecrawl_api_key: str = ""
    firecrawl_api_url: str = "https://api.firecrawl.dev/v1"
    search_providers: str = "tavily"

    # Grievance & evidence
    grievance_gemini_model: str = "gemini-3.5-flash-lite"

    # Clerk authentication
    clerk_secret_key: str = ""
    clerk_webhook_secret: str = ""
    clerk_issuer: str = ""  # e.g. "https://clerk.your-app.com"

    # Answer grounding
    answer_grounding_llm_enabled: bool = False  # Enable LLM verification layer

    # Web RAG latency budgets
    gemini_reranker_timeout_s: float = 8.0
    jina_reranker_timeout_s: float = 5.0
    web_rag_timeout_s: float = 30.0

    @property
    def tts_voices(self) -> dict[str, str]:
        """Parse azure_tts_voices into a dict."""
        result: dict[str, str] = {}
        for pair in self.azure_tts_voices.split(","):
            if ":" in pair:
                lang, voice = pair.split(":", 1)
                result[lang.strip()] = voice.strip()
        return result

    @property
    def speech_locales(self) -> dict[str, str]:
        """Parse azure_speech_locales into a dict {language: BCP-47 locale}."""
        result: dict[str, str] = {}
        for pair in self.azure_speech_locales.split(","):
            if ":" in pair:
                lang, loc = pair.split(":", 1)
                result[lang.strip()] = loc.strip()
        return result

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def sarvam_keys(self) -> list[str]:
        """Return all non-empty Sarvam API keys for rotation."""
        keys = [k for k in [self.sarvam_api_key, self.sarvam_api_key_2] if k]
        return keys

    @property
    def groq_keys(self) -> list[str]:
        """Return all non-empty Groq API keys for rotation."""
        keys = [k for k in [self.groq_api_key, self.groq_api_key_1, self.groq_api_key_2] if k]
        return keys

    @property
    def jina_keys(self) -> list[str]:
        """Return all non-empty Jina API keys for rotation."""
        keys = [k for k in [self.jina_api_key, self.jina_api_key_2] if k]
        return keys

EMBED_DIMS = 768
REQUEST_TIMEOUT_S = 30.0

# Generation limits (transplanted from eGovAssistant proven defaults)
GENERATION_MAX_TOKENS = 1800
GENERATION_TEMPERATURE = 0.0
MAX_CHARS_PER_CHUNK = 3000

# Retrieval gate thresholds (spec §2.4)
TOP1_THRESHOLD = 0.25
SECONDARY_THRESHOLD = 0.30
MIN_CHUNKS_ABOVE_SECONDARY = 2

@lru_cache
def get_settings() -> Settings:
    return Settings()
