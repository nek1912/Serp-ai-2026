"""
Web discovery service.

Architecture:

    Query
      ↓
    Existing QueryClassifier
      ↓
    Jurisdiction resolution
      ↓
    Official-source Tavily search
      ↓
    Web document cleaning
      ↓
    Structure-aware web chunking
      ↓
    BM25 ranking
      ↓
    Evidence threshold
      ↓
    Optional broader trusted-web search
      ↓
    Final web candidates

This module does NOT generate answers.

IMPORTANT:
The QueryClassifier remains the single source of truth for
query classification.

The classification may either be:
    1. supplied by the caller, or
    2. created internally when no classification is supplied.

This allows /chat → RAGPipeline → WebDiscoveryService to
share the SAME classification without creating another
classifier.
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urlparse

from app.web_rag.bm25_ranker import WebBM25Ranker
from app.web_rag.firecrawl_client import FirecrawlClient
from app.web_rag.providers import resolve_providers
from app.web_rag.query_classifier import (
    QueryClassification,
    QueryClassifier,
)
from app.web_rag.retrieval_scorer import rescore
from app.web_rag.scheme_candidate import (
    identify_scheme_candidate,
    boost_scheme_candidate,
)
from app.web_rag.tavily_client import TavilyClient
from app.web_rag.web_cleaner import (
    WebDocumentCleaner,
)

logger = logging.getLogger(__name__)


OFFICIAL_DOMAINS = [
    "gov.in",
    "nic.in",
    "india.gov.in",
    "digitalindia.gov.in",
    "meity.gov.in",
    "pmfby.gov.in",
    "cooperation.gov.in",
    "cooperatives.gov.in",
    "agricoop.gov.in",
    "agricoop.nic.in",
    "farmer.gov.in",
    "agmarknet.gov.in",
    "enam.gov.in",
    "nafscob.org",
    "ncui.coop",
    "iffco.in",
    "kribhco.net",
    "guj.nic.in",
    "gujaratcooperative.gov.in",
    "cooperation.gujarat.gov.in",
    "maharashtra.gov.in",
    "cooperation.mp.gov.in",
    "nabard.org",
    "rbi.org.in",
    "sidbi.in",
    "mudra.org.in",
    "pmjdy.gov.in",
    "pgportal.gov.in",
    "cpgrams.gov.in",
    "serviceonline.gov.in",
    "irdai.gov.in",
    "mca.gov.in",
    "pib.gov.in",
    "mygov.in",
    "nsdcindia.org",
    "pmkvyofficial.org",
    "pmkvyproject.org",
    "aicte-india.org",
    "npscra.nsdl.co.in",
    "nsdl.co.in",
    "onlineservices.nsdl.com",
    "pfrda.org.in",
    "nsiindia.gov.in",
    "jansuraksha.gov.in",
    "pahal-diksha.gov.in",
    "parivahan.gov.in",
]


TRUSTED_SECONDARY_DOMAINS = [

    "prsindia.org",
    "indiacode.nic.in",
    "sci.gov.in",
    "niti.gov.in",

]


WEB_CHUNK_SIZE = 1500
WEB_CHUNK_OVERLAP = 250

WEB_MAX_CHUNKS_PER_SOURCE = 12


class WebDiscoveryService:

    def __init__(
        self,
        tavily_client: TavilyClient | None = None,
        firecrawl_client: FirecrawlClient | None = None,
        bm25_ranker: WebBM25Ranker | None = None,
        classifier: QueryClassifier | None = None,
        web_cleaner: WebDocumentCleaner | None = None,
    ):

        self.tavily = (
            tavily_client
            if tavily_client is not None
            else TavilyClient()
        )

        self.search_providers = []

        for provider in resolve_providers():

            if isinstance(
                provider,
                TavilyClient,
            ):

                if self.tavily not in self.search_providers:
                    self.search_providers.append(
                        self.tavily
                    )

                continue

            if provider not in self.search_providers:
                self.search_providers.append(
                    provider
                )

        if not self.search_providers:

            self.search_providers.append(
                self.tavily
            )

        if (
            firecrawl_client is not None
            and firecrawl_client not in self.search_providers
        ):
            self.search_providers.append(
                firecrawl_client
            )

        self.firecrawl = (
            firecrawl_client
            if firecrawl_client is not None
            else FirecrawlClient()
        )

        self.bm25 = (
            bm25_ranker
            if bm25_ranker is not None
            else WebBM25Ranker()
        )

        self.classifier = (
            classifier
            if classifier is not None
            else QueryClassifier()
        )

        self.cleaner = (
            web_cleaner
            if web_cleaner is not None
            else WebDocumentCleaner()
        )


    @staticmethod
    def _domain(
        url: str,
    ) -> str:

        try:

            return (
                urlparse(url)
                .netloc
                .lower()
                .split(":")[0]
                .removeprefix("www.")
            )

        except Exception:

            return ""


    @classmethod
    def is_official_url(
        cls,
        url: str,
    ) -> bool:

        domain = cls._domain(
            url
        )

        if not domain:
            return False

        for official in OFFICIAL_DOMAINS:

            if (
                domain == official
                or domain.endswith(
                    "." + official
                )
            ):

                return True

        return False


    @classmethod
    def is_trusted_secondary(
        cls,
        url: str,
    ) -> bool:

        domain = cls._domain(
            url
        )

        if not domain:
            return False

        for trusted in TRUSTED_SECONDARY_DOMAINS:

            if (
                domain == trusted
                or domain.endswith(
                    "." + trusted
                )
            ):

                return True

        return False


    @staticmethod
    def _build_query(
        query: str,
        classification: QueryClassification,
    ) -> str:

        additions = []

        if classification.domain != "general":

            additions.append(
                classification.domain
            )

        if classification.state:

            additions.append(
                classification.state
            )

        if additions:

            return (
                f"{query} "
                + " ".join(additions)
            )

        return query


    @staticmethod
    def _clean_title(
        text: str,
    ) -> str:

        text = str(
            text or ""
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip()


    @staticmethod
    def _make_chunk_id(
        url: str,
        index: int,
    ) -> str:

        digest = hashlib.sha1(
            url.encode(
                "utf-8"
            )
        ).hexdigest()[:12]

        return (
            f"web_{digest}_c{index}"
        )


    def _normalize_results(
        self,
        response: dict,
    ) -> list[dict]:

        normalized = []

        raw_results = response.get(
            "results",
            [],
        )

        if not isinstance(
            raw_results,
            list,
        ):

            return []

        for result_index, item in enumerate(
            raw_results,
            start=1,
        ):

            if not isinstance(
                item,
                dict,
            ):

                continue

            url = str(
                item.get(
                    "url",
                    "",
                )
            ).strip()

            if not url:
                continue

            title = self._clean_title(
                item.get(
                    "title",
                    "",
                )
            )

            content = str(
                item.get(
                    "content",
                    "",
                )
                or ""
            )

            raw_content = str(
                item.get(
                    "raw_content",
                    "",
                )
                or ""
            )

            source_text = (
                raw_content
                if raw_content.strip()
                else content
            )

            if not source_text.strip():
                continue

            cleaned_text = (
                self.cleaner.clean(
                    source_text
                )
            )

            if (
                len(cleaned_text.strip()) < 80
                and content.strip()
                and content != raw_content
            ):

                cleaned_text = (
                    self.cleaner.clean(
                        content
                    )
                )

            if not cleaned_text.strip():
                continue

            chunks = self.cleaner.chunk(
                cleaned_text,
                chunk_size=WEB_CHUNK_SIZE,
                overlap=WEB_CHUNK_OVERLAP,
                max_chunks=WEB_MAX_CHUNKS_PER_SOURCE,
            )

            if not chunks:
                continue

            for chunk_index, chunk in enumerate(
                chunks,
                start=1,
            ):

                chunk_id = (
                    self._make_chunk_id(
                        url,
                        (
                            result_index * 100
                            + chunk_index
                        ),
                    )
                )

                official = (
                    self.is_official_url(
                        url
                    )
                )

                trusted_secondary = (
                    self.is_trusted_secondary(
                        url
                    )
                )

                normalized.append(
                    {
                        "chunk_id": chunk_id,

                        "document_id": url,

                        "title": (
                            title
                            or url
                        ),

                        "file_name": None,

                        "issuer": (
                            "Government / Official Web Source"
                            if official
                            else None
                        ),

                        "section_title": (
                            "Web page"
                        ),

                        "section": (
                            "Web page"
                        ),

                        "section_type": (
                            "web"
                        ),

                        "page": None,

                        "source": "web",

                        "source_type": "url",

                        "source_url": url,

                        "url": url,

                        "text": chunk,

                        "content": chunk,

                        "web_title": title,

                        "tavily_score": float(
                            item.get(
                                "score",
                                0.0,
                            )
                            or 0.0
                        ),

                        "favicon": item.get(
                            "favicon"
                        ),

                        "official": official,

                        "trusted_secondary": (
                            trusted_secondary
                        ),

                        "retrieval_stage": (
                            "official"
                            if official
                            else "web"
                        ),
                    }
                )

        return normalized


    SERVICE_PATH_INTENTS = {
        "APPLICATION",
        "REGISTRATION",
        "ELIGIBILITY",
        "DOCUMENT_REQUIREMENTS",
        "STATUS",
        "SERVICE_ACCESS",
        "BENEFIT",
        "SUBSIDY_AMOUNT",
        "GRIEVANCE",
        "CONTACT",
        "DEADLINE",
    }

    _SERVICE_PATH_QUERY_REGEXES = [
        re.compile(r"\b(how\s+to\s+(apply|register|enroll|access|log\s*in|file|book|track))\b", re.IGNORECASE),
        re.compile(r"\b(how\s+(can|do|does)\s+((i|anyone|a|an|farmer|student|citizen)\s+)?(apply|register|enroll|access|get|file|book|check))\b", re.IGNORECASE),
        re.compile(r"\b(apply|register|enroll|sign\s*up|log\s*in|login|file\s+a|book\s+an|lodge)\b", re.IGNORECASE),
        re.compile(r"\b(apply\s+for|register\s+for|process\s+to\s+(apply|register))\b", re.IGNORECASE),
        re.compile(r"\b(check\s+(status|balance|progress)|track|status of)\b", re.IGNORECASE),
        re.compile(r"\b(application|registration|enrollment|appointment|complaint)\b", re.IGNORECASE),
        re.compile(r"\b(portal|website|online)\b", re.IGNORECASE),
        re.compile(r"\b(documents required|what documents|requirements|eligibility criteria)\b", re.IGNORECASE),
        re.compile(r"\b(who\s+(is|are)\s+(eligible|registered)|am\s+i\s+eligible)\b", re.IGNORECASE),
    ]

    _SERVICE_PATH_URL_REGEXES = [
        re.compile(r"\b(login|log\s*in)\b", re.IGNORECASE),
        re.compile(r"\b(apply|application|applicant|register|registration|enroll|enrollment)\b", re.IGNORECASE),
        re.compile(r"\b(status|tracking|track|check)\b", re.IGNORECASE),
        re.compile(r"\b(portal)\b", re.IGNORECASE),
        re.compile(r"\b(form|forms)\b", re.IGNORECASE),
        re.compile(r"\b(faq|help|support|contact)\b", re.IGNORECASE),
    ]

    _SERVICE_PATH_TITLE_REGEXES = [
        re.compile(r"\b(how to (apply|register|enroll|log in|check status))\b", re.IGNORECASE),
        re.compile(r"\b(apply online|online application|online registration|registration form|application form)\b", re.IGNORECASE),
        re.compile(r"\b(check status|track status|track application|track your)\b", re.IGNORECASE),
        re.compile(r"\b(login|sign in|sign up|register now)\b", re.IGNORECASE),
    ]

    _CURRENT_QUERY_REGEX = re.compile(
        r"\b(latest|current|now|update|updated|recent|new|202[3-9])\b",
        re.IGNORECASE,
    )

    def _assess_official_evidence(
        self,
        query: str,
        results: list[dict],
        classification: QueryClassification,
    ) -> dict:
        """
        Decide whether Stage-1 official-only results are SUFFICIENT for
        the user's actual query intent, or whether Stage-2 broad web
        discovery should run.

        This is a GENERIC, deterministic assessment. It never tries to
        identify the exact ground-truth source/URL. It only answers:
        "do the retrieved official results give enough evidence that a
        further search stage is unnecessary?".

        It reuses existing classification metadata (intent,
        jurisdiction, state) and requires NO additional API / LLM call.

        Returns a dict of diagnostic metadata used both to make the
        Stage-2 decision and to log reasons (STEP 8).
        """

        official = [
            result
            for result in results
            if result.get(
                "official",
                False,
            )
        ]

        unique_urls = {
            result.get(
                "source_url"
            )
            for result in official
            if result.get(
                "source_url"
            )
        }

        intent = (
            classification.intent
            or "INFORMATIONAL"
        ).upper()

        state = (
            classification.state
            or ""
        ).lower()

        jurisdiction = (
            classification.jurisdiction
            or "central"
        ).lower()

        query_tokens = {
            token
            for token in re.findall(
                r"\b\w+\b",
                query.lower(),
            )
            if len(token) >= 4
        }

        best_overlap = 0.0
        best_official = None

        for result in official:

            text = (
                str(
                    result.get(
                        "title",
                        "",
                    )
                )
                + " "
                + str(
                    result.get(
                        "text",
                        "",
                    )
                )
            ).lower()

            source_tokens = set(
                re.findall(
                    r"\b\w+\b",
                    text,
                )
            )

            overlap = (
                len(
                    query_tokens
                    & source_tokens
                )
                / len(query_tokens)
                if query_tokens
                else 0.0
            )

            if overlap > best_overlap:
                best_overlap = overlap
                best_official = result

        query_needs_service = (
            intent in self.SERVICE_PATH_INTENTS
            or any(
                rx.search(query)
                for rx in self._SERVICE_PATH_QUERY_REGEXES
            )
        )

        stage1_has_service_path = False
        service_path_hits = 0
        for result in official:
            url_text = str(result.get("source_url") or "")
            title_text = str(result.get("title") or "")
            url_hits = sum(
                1
                for rx in self._SERVICE_PATH_URL_REGEXES
                if rx.search(url_text)
            )
            title_hits = sum(
                1
                for rx in self._SERVICE_PATH_TITLE_REGEXES
                if rx.search(title_text)
            )
            hits = url_hits + title_hits
            if url_hits > 0 or title_hits > 0:
                stage1_has_service_path = True
                service_path_hits += hits

        is_current_query = bool(
            self._CURRENT_QUERY_REGEX.search(query)
        )

        stage1_has_recent_signal = False
        if is_current_query:
            for result in official:
                if re.search(
                    r"\b(202[0-9]|20[2-9][0-9])\b",
                    str(result.get("title") or "")
                    + " "
                    + str(result.get("text") or ""),
                ):
                    stage1_has_recent_signal = True
                    break

        is_state_query = (
            jurisdiction == "state"
            and bool(state)
        )

        stage1_matches_jurisdiction = (
            not is_state_query
        )  # central queries are always jurisdiction-agnostic here

        if is_state_query:
            state_needle = state.replace(" ", "")
            for result in official:
                haystack = (
                    str(result.get("source_url") or "")
                    + " "
                    + str(result.get("title") or "")
                ).lower().replace(" ", "")
                if (
                    state_needle in haystack
                    or state in (
                        str(result.get("source_url") or "")
                        + " "
                        + str(result.get("title") or "")
                    ).lower()
                ):
                    stage1_matches_jurisdiction = True
                    break

        stage1_result_count = len(official)
        n_unique = len(unique_urls)

        if n_unique < 1 or best_overlap < 0.15:
            return {
                "sufficient": False,
                "reason": "insufficient_baseline",
                "intent": intent,
                "needs_service_path": query_needs_service,
                "has_service_path": stage1_has_service_path,
                "is_current_query": is_current_query,
                "is_state_query": is_state_query,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        multi_source = n_unique >= 2

        if query_needs_service and not stage1_has_service_path:
            return {
                "sufficient": False,
                "reason": "intent_needs_service_path",
                "intent": intent,
                "needs_service_path": True,
                "has_service_path": False,
                "is_current_query": is_current_query,
                "is_state_query": is_state_query,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        # A state-specific service query must also have evidence matching
        if is_state_query and not stage1_matches_jurisdiction:
            return {
                "sufficient": False,
                "reason": "jurisdiction_mismatch",
                "intent": intent,
                "needs_service_path": query_needs_service,
                "has_service_path": stage1_has_service_path,
                "is_current_query": is_current_query,
                "is_state_query": True,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": False,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        if is_current_query and not stage1_has_recent_signal:
            return {
                "sufficient": False,
                "reason": "needs_recent_information",
                "intent": intent,
                "needs_service_path": query_needs_service,
                "has_service_path": stage1_has_service_path,
                "is_current_query": True,
                "is_state_query": is_state_query,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        if not query_needs_service and best_overlap >= 0.25:
            return {
                "sufficient": True,
                "reason": "informational_satisfied",
                "intent": intent,
                "needs_service_path": False,
                "has_service_path": stage1_has_service_path,
                "is_current_query": is_current_query,
                "is_state_query": is_state_query,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        if (
            multi_source
            and stage1_has_service_path
            and best_overlap >= 0.40
        ):
            return {
                "sufficient": True,
                "reason": "service_path_satisfied",
                "intent": intent,
                "needs_service_path": True,
                "has_service_path": True,
                "is_current_query": is_current_query,
                "is_state_query": is_state_query,
                "stage1_result_count": stage1_result_count,
                "n_unique": n_unique,
                "best_overlap": round(best_overlap, 4),
                "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
                "best_official_url": (
                    best_official.get("source_url")
                    if best_official
                    else None
                ),
            }

        return {
            "sufficient": False,
            "reason": "insufficient_actionable_evidence",
            "intent": intent,
            "needs_service_path": query_needs_service,
            "has_service_path": stage1_has_service_path,
            "is_current_query": is_current_query,
            "is_state_query": is_state_query,
            "stage1_result_count": stage1_result_count,
            "n_unique": n_unique,
            "best_overlap": round(best_overlap, 4),
            "stage1_matches_jurisdiction": stage1_matches_jurisdiction,
            "best_official_url": (
                best_official.get("source_url")
                if best_official
                else None
            ),
        }


    def _search_all(
        self,
        query: str,
        *,
        domain: str | None = None,
        state: str | None = None,
        max_results: int = 20,
        chunks_per_source: int = 3,
        include_domains: list[str] | None = None,
        search_depth: str = "advanced",
        include_raw_content: bool = True,
        only_official: bool = False,
    ) -> list[dict]:
        """
        Query every active search provider for ``query`` and merge their
        raw results, de-duplicating by source URL.
        """
        seen_urls: set[str] = set()
        merged: list[dict] = []

        for provider in self.search_providers:

            try:

                response = provider.search(
                    query,
                    domain=domain,
                    state=state,
                    max_results=max_results,
                    chunks_per_source=chunks_per_source,
                    include_domains=include_domains,
                    search_depth=search_depth,
                    include_raw_content=include_raw_content,
                )

            except Exception as exc:
                # A single provider failing must not break discovery, but
                # hide neither the reason nor the affected provider.
                logger.warning(
                    "Web discovery provider failed: provider=%s error=%s",
                    type(provider).__name__,
                    str(exc)[:300],
                )
                continue

            items = (
                response.get(
                    "results",
                    [],
                )
                if isinstance(
                    response,
                    dict,
                )
                else []
            )

            for item in items:

                url = str(
                    item.get(
                        "url",
                        "",
                    )
                ).strip()

                if not url:
                    continue

                if only_official and not self.is_official_url(url):
                    continue

                if url in seen_urls:
                    continue

                seen_urls.add(
                    url
                )

                merged.append(
                    item
                )

        return merged


    def discover(
        self,
        query: str,
        classification: QueryClassification | None = None,
    ) -> dict:

        query = str(
            query or ""
        ).strip()

        if not query:

            raise ValueError(
                "Web discovery query cannot be empty."
            )


        if classification is None:

            classification = (
                self.classifier.classify(
                    query
                )
            )

        enriched_query = (
            self._build_query(
                query,
                classification,
            )
        )


        official_search_items = (
            self._search_all(
                enriched_query,
                domain=classification.domain,
                state=classification.state,
                max_results=20,
                chunks_per_source=3,
                include_domains=OFFICIAL_DOMAINS,
                search_depth="advanced",
                include_raw_content=True,
                only_official=True,
            )
        )

        official_response = {
            "results": official_search_items
        }

        official_chunks = (
            self._normalize_results(
                official_response
            )
        )

        official_ranked = (
            self.bm25.rank(
                query=query,
                results=official_chunks,
                top_k=40,
            )
        )


        evidence_assessment = (
            self._assess_official_evidence(
                query=query,
                results=official_ranked,
                classification=classification,
            )
        )

        official_enough = bool(
            evidence_assessment.get(
                "sufficient",
                False,
            )
        )

        # provider failures / 429 / timeout / empty results must never

        stage2_result_count = 0
        stage2_provider_failures = 0
        stage2_went_empty = False
        stage2_trigger_reason = None

        if official_enough:

            final_results = (
                official_ranked
            )

            discovery_stage = (
                "official"
            )

            fallback_used = False

        else:

            stage2_trigger_reason = (
                evidence_assessment.get(
                    "reason",
                    "insufficient_actionable_evidence",
                )
            )

            try:

                broad_search_items = (
                    self._search_all(
                        enriched_query,
                        domain=classification.domain,
                        state=classification.state,
                        max_results=20,
                        chunks_per_source=3,
                        search_depth="advanced",
                        include_raw_content=True,
                        only_official=False,
                    )
                )

                broad_response = {
                    "results": broad_search_items
                }

                broad_chunks = (
                    self._normalize_results(
                        broad_response
                    )
                )

                existing_urls = {
                    result.get(
                        "source_url"
                    )
                    for result in official_ranked
                }

                fallback_chunks = [
                    result
                    for result in broad_chunks
                    if result.get(
                        "source_url"
                    )
                    not in existing_urls
                ]

                stage2_result_count = len(
                    fallback_chunks
                )

                if not fallback_chunks:

                    stage2_went_empty = True
                    final_results = official_ranked
                    discovery_stage = "official"
                    fallback_used = False

                else:

                    combined = (
                        official_ranked
                        + fallback_chunks
                    )

                    final_results = (
                        self.bm25.rank(
                            query=query,
                            results=combined,
                            top_k=40,
                        )
                    )

                    discovery_stage = (
                        "official_plus_web"
                    )

                    fallback_used = True

            except Exception:

                # Any Stage-2 error must not erase Stage-1 results.
                stage2_provider_failures += 1
                stage2_went_empty = True
                final_results = official_ranked
                discovery_stage = "official"
                fallback_used = False


        final_results = (
            rescore(
                query=query,
                results=final_results,
                classification=classification,
                top_k=20,
            )
        )


        scheme_candidate_data = None

        if (
            classification
            and getattr(
                classification, "domain", None
            )
            == "schemes"
        ):

            scheme_candidate = (
                identify_scheme_candidate(
                    query=query,
                    results=final_results,
                )
            )

            if scheme_candidate is not None:

                final_results = (
                    boost_scheme_candidate(
                        results=final_results,
                        candidate=scheme_candidate,
                    )
                )

                scheme_candidate_data = {
                    "url": (
                        scheme_candidate.get(
                            "source_url"
                        )
                        or scheme_candidate.get("url")
                    ),
                    "score": (
                        scheme_candidate.get(
                            "scheme_candidate_score"
                        )
                    ),
                    "reasons": (
                        scheme_candidate.get(
                            "scheme_candidate_reasons"
                        )
                    ),
                }

                print(
                    f"\nScheme candidate identified: "
                    f"{scheme_candidate_data['url']}"
                )

                print(
                    f"Scheme candidate score: "
                    f"{scheme_candidate_data['score']}"
                )


        for result in final_results:

            result[
                "query_domain"
            ] = classification.domain

            result[
                "jurisdiction"
            ] = classification.jurisdiction

            result[
                "state"
            ] = classification.state

            result[
                "classification_confidence"
            ] = classification.confidence

            result[
                "discovery_stage"
            ] = discovery_stage

            result[
                "official_search"
            ] = bool(
                result.get(
                    "official",
                    False,
                )
            )

        return {

            "query": query,

            "search_query": enriched_query,

            "classification": {

                "domain": classification.domain,

                "jurisdiction": (
                    classification.jurisdiction
                ),

                "state": classification.state,

                "confidence": (
                    classification.confidence
                ),

                "query_intent": (
                    evidence_assessment.get(
                        "intent",
                        classification.intent,
                    )
                ),
            },

            "discovery_stage": (
                discovery_stage
            ),

            "fallback_used": (
                fallback_used
            ),

            "official_evidence_sufficient": (
                official_enough
            ),

            "discovery_metadata": {
                "stage1_result_count": (
                    evidence_assessment.get(
                        "stage1_result_count",
                        0,
                    )
                ),
                "stage1_sufficiency": (
                    "sufficient"
                    if official_enough
                    else "insufficient"
                ),
                "stage1_sufficiency_reason": (
                    evidence_assessment.get(
                        "reason",
                        "unknown",
                    )
                ),
                "query_intent": (
                    evidence_assessment.get(
                        "intent",
                        classification.intent,
                    )
                ),
                "needs_service_path": bool(
                    evidence_assessment.get(
                        "needs_service_path",
                        False,
                    )
                ),
                "has_service_path": bool(
                    evidence_assessment.get(
                        "has_service_path",
                        False,
                    )
                ),
                "is_current_query": bool(
                    evidence_assessment.get(
                        "is_current_query",
                        False,
                    )
                ),
                "is_state_query": bool(
                    evidence_assessment.get(
                        "is_state_query",
                        False,
                    )
                ),
                "stage1_best_overlap": (
                    evidence_assessment.get(
                        "best_overlap",
                        0.0,
                    )
                ),
                "stage1_n_unique": (
                    evidence_assessment.get(
                        "n_unique",
                        0,
                    )
                ),
                "stage1_matches_jurisdiction": bool(
                    evidence_assessment.get(
                        "stage1_matches_jurisdiction",
                        True,
                    )
                ),
                "stage2_triggered": (
                    bool(stage2_trigger_reason)
                ),
                "stage2_trigger_reason": (
                    stage2_trigger_reason
                ),
                "stage2_result_count": (
                    stage2_result_count
                ),
                "stage2_went_empty": (
                    stage2_went_empty
                ),
                "stage2_provider_failures": (
                    stage2_provider_failures
                ),
            },

            "results": final_results,

            "scheme_candidate": (
                scheme_candidate_data
            ),
        }
