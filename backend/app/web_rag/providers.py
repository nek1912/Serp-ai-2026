"""
Search-provider factory for the WebDiscovery layer.

The WebDiscoveryService performs web search through one or more
providers. This module centralises HOW providers are chosen so that
swapping or adding a search engine later requires NO pipeline changes:

  * set ``SEARCH_PROVIDERS`` in the backend .env file to a
    comma-separated list of provider names (e.g. ``tavily,firecrawl``);
  * configure that provider's API key in .env;
  * add a client class + one registration line below.

Every client must expose a uniform ``.search(query, *, max_results,
chunks_per_source, include_domains, exclude_domains, search_depth,
include_raw_content) -> {"results": [...]}`` interface so the service
and its normalizer stay engine-agnostic.
"""

from __future__ import annotations

import os
from typing import Any
from pathlib import Path

from dotenv import load_dotenv

from app.web_rag.firecrawl_client import (
    FirecrawlClient,
)
from app.web_rag.serpapi_client import (
    SerpApiClient,
)
from app.web_rag.tavily_client import (
    TavilyClient,
)


# Point at the repo-root .env explicitly. A bare load_dotenv() resolves `.env`
# against the process CWD, so it picked up the stale backend/.env when uvicorn
# runs from backend/ and pushed its values into os.environ — where pydantic
# gives them priority over the env_file. That is how ALLOWED_ORIGINS reverted to
# the old Next.js port and every cross-origin browser request was rejected.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


DEFAULT_PROVIDERS = "tavily"


_PROVIDER_FACTORIES = {
    "tavily": lambda: TavilyClient(),
    "firecrawl": lambda: FirecrawlClient(),
    "serpapi": lambda: SerpApiClient(),
}


def resolve_providers(
    raw: str | None = None,
) -> list[Any]:

    if raw is None:

        raw = os.getenv(
            "SEARCH_PROVIDERS",
            "",
        )

    raw = (raw or "").strip()

    names = (
        [p.strip().lower() for p in raw.split(",") if p.strip()]
        if raw
        else [n for n in DEFAULT_PROVIDERS.split(",") if n]
    )

    providers = []

    for name in names:

        factory = (
            _PROVIDER_FACTORIES.get(
                name,
            )
        )

        if factory is None:

            continue

        client = factory()

        configure = getattr(
            client,
            "is_configured",
            None,
        )

        if (
            configure is not None
            and not configure()
        ):

            continue

        providers.append(
            client
        )

    if not providers:

        providers = [
            TavilyClient(),
        ]

    return providers
