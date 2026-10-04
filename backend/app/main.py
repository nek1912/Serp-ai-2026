import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes.chat import router as chat_router
from app.routes.voice import router as voice_router
from app.routes.conversations import router as conversations_router
from app.routes.evidence import router as evidence_router
from app.routes.grievance import router as grievance_router
from app.routes.documents import router as documents_router
from app.routes.translate import router as translate_router
from app.routes.webhooks import router as webhooks_router

logging.basicConfig(level=logging.INFO,
                    format='{"level":"%(levelname)s","msg":"%(message)s"}')


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Sahayak API", version="0.1.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware,
                   allow_origins=get_settings().origins,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(conversations_router)
app.include_router(evidence_router)
app.include_router(grievance_router)
app.include_router(documents_router)
app.include_router(translate_router)
app.include_router(webhooks_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/health/providers")
def health_providers() -> dict:
    s = get_settings()
    return {
        "groq": "configured" if s.groq_api_key else "missing",
        "gemini": "configured" if s.gemini_api_key else "missing",
        "supabase": "configured" if s.supabase_url else "missing",
        "bhashini": "stub",
        "azure_speech": "configured" if s.azure_speech_key else "missing",
        "tavily": "configured" if s.tavily_api_key_1 else "missing",
        "firecrawl": "configured" if s.firecrawl_api_key else "missing",
    }
