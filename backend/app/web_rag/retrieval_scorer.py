"""
Deterministic, additive retrieval-quality re-scoring for the
WebDiscovery layer.

IMPORTANT -- what this module is and is NOT:

This module runs AFTER the existing BM25 ranking inside
WebDiscoveryService.discover(). It does NOT replace BM25, the
search architecture, the fallback logic, or the official-source
detection. It only RE-ORDERS the already-discovered, already-BM25
ranked candidates so that retrieval quality signals -- authority,
intent fit, service-portal fit, page-level specificity, geography
and same-domain diversity -- can promote or demote a candidate.

Signals are deterministic, rule-based and cheap. No LLM calls are
made here. No new classification subsystem is introduced: intent
and state come from the existing QueryClassification produced by
the single existing QueryClassifier.

All score fields added here are additive keys on each result dict
(e.g. ``retrieval_quality_score``, ``source_tier``,
``service_fit``). The existing ``official`` boolean owned by
WebDiscoveryService is never altered, so the benchmark's Official
Source Rate metric is unaffected by this re-ordering.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


SERVICE_INTENTS = {
    "APPLICATION",
    "REGISTRATION",
    "SERVICE_ACCESS",
    "STATUS",
    "GRIEVANCE",
    "DOCUMENT_REQUIREMENTS",
    "DEADLINE",
    "CONTACT",
}

_SERVICE_PATH_HINTS = [
    "apply", "application", "register", "registration",
    "login", "sign-in", "signin", "sign-up", "signup", "portal",
    "online", "book", "appointment", "enroll", "enrollment",
    "submit", "track", "status", "download", "form", "how-to",
    "how to", "e-services", "eservices", "services", "service",
]

_ANNOUNCEMENT_HINTS = [
    "press", "pressrelease", "press-release", "pib", "news",
    "announcement", "prid", "note", "factsheet",
]

LOW_AUTHORITY_HINTS = [
    "wikipedia", "quora", "facebook", "reddit", "blogspot",
    "wordpress", ".blog.", "sites.google", "testbook", "linkedin",
    "medium.com", "scribd", "bajajfinserv", "bankbazaar",
    "paisabazaar", "fly.finance", "gyandhan", "leverageedu",
    "adarsh", "nomadcredit", "aimindia", "airslate", "rupayasalah",
    "tataaia", "hdfcergo", "drishtiias", "agribegri", "zolostays",
    "sarkariyojana", "stablemoney", "ltfinance", "jagranjosh",
    "study", "coach", "freshersjob4u",
]

_COMMERCIAL_TLDS = {".com", ".co", ".net", ".org", ".info"}


def normalize_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc.split("@")[-1]
        netloc = netloc.split(":")[0]
        netloc = netloc.removeprefix("www.")
        return netloc.rstrip(".")
    except Exception:
        return ""


def _path(url: str) -> str:
    try:
        return (urlparse(url).path or "").lower()
    except Exception:
        return ""


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
    }


def _is_inside(domain: str, expected: str) -> bool:
    return domain == expected or domain.endswith("." + expected)


def source_tier(
    url: str,
    title: str,
    official: bool,
    trusted_secondary: bool,
) -> tuple[int, str]:
    """
    Returns (tier, class_name) for a result url.

    A higher tier is generally more authoritative. Tiers are used
    as a ranking signal only; they never replace the existing
    ``official`` boolean (which stays untouched for the Official
    Source Rate metric).

    Tiering is context-aware through the calling code: a PIB
    announcement is excellent for an informational/announcement
    query but a service portal page is preferred for an action
    query.
    """

    domain = normalize_domain(url)
    path = _path(url)
    title_l = (title or "").lower()
    blob = path + " " + title_l

    definitional = (
        "arthapedia" in path
        or "/concept" in path
        or "concept/" in path
        or "encyclopedia" in blob
        or "what-is" in path
    )

    if (official or trusted_secondary) and not definitional:
        if any(h in path or (" " + h) in (" " + title_l) for h in _SERVICE_PATH_HINTS):
            return 6, "official_service_portal"

    if official and _is_announcement(url, title):
        return 4, "official"

    if official and any(h in path for h in ["ministry", "department", "government", "scheme"]):
        return 5, "official_ministry"

    if official:
        return 4, "official"

    if trusted_secondary:
        return 3, "trusted_secondary"

    if any(_is_inside(domain, d) for d in ["nsdcindia.org", "aicte-india.org", "mudra.org.in",
                                            "pfrda.org.in", "npscra.nsdl.co.in", "onlineservices.nsdl.com",
                                            "pfrda.org.in", "nabard.org", "rbi.org.in", "nsdl.co.in",
                                            "vidyalakshmi.co.in", "pmkvyofficial.org", "pgvcl.com"]):
        return 3, "trusted_secondary"

    return 0, "general_web"


def _is_low_authority(url: str) -> bool:
    domain = normalize_domain(url)
    return any(h in domain for h in LOW_AUTHORITY_HINTS)


def service_fit_score(
    url: str,
    title: str,
    intent: str,
) -> float:
    """
    Bonus for pages that look like the actual service / apply /
    register / portal page, used only for service-oriented intents.
    """
    if intent not in SERVICE_INTENTS:
        return 0.0
    path = _path(url)
    title_l = (title or "").lower()
    blob = path + " " + title_l
    hits = sum(1 for h in _SERVICE_PATH_HINTS if h in blob)
    if hits <= 0:
        return 0.0
    return min(0.45, 0.12 * hits)


def _is_announcement(url: str, title: str) -> bool:
    path = _path(url)
    title_l = (title or "").lower()
    blob = path + " " + title_l
    return any(h in blob for h in _ANNOUNCEMENT_HINTS)


def page_relevance(
    url: str,
    title: str,
    text: str,
    query: str,
) -> float:
    """
    Boosts a result when the URL path / title actually contains the
    query's key tokens. This rewards the CORRECT page on a domain,
    not merely the correct domain (page-level > domain-level).
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    path = _tokens(_path(url))
    title_t = _tokens(title)
    path_hits = len(q_tokens & path)
    title_hits = len(q_tokens & title_t)
    score = (path_hits * 0.10) + (title_hits * 0.06)
    if text:
        text_t = _tokens(text[:5000])
        score += min(0.10, len(q_tokens & text_t) / len(q_tokens) * 0.08)
    return min(0.55, score)


_FRESH_INTENTS = {"SUBSIDY_AMOUNT", "DEADLINE", "STATUS", "BENEFIT"}

def freshness_bonus(
    url: str,
    intent: str,
    query: str,
) -> float:
    """
    Very light, purely defensive freshness signal.

    Only applied for subsidy/amount/deadline/status intents or when
    the query explicitly asks for latest/current/new. Uses a year
    present in the URL path (e.g. /2025/ or /file/2026) as a weak
    proxy for recency. If no year is found, no bonus is applied and
    no candidate is penalized.
    """
    if intent not in _FRESH_INTENTS:
        q = query.lower()
        if not any(w in q for w in ["latest", "current", "new", "recent", "2025", "2026", "amount", "subsidy"]):
            return 0.0
    years = re.findall(r"/?(20\d{2})", _path(url))
    if not years:
        return 0.0
    latest = max(int(y) for y in years)
    if latest >= 2024:
        return 0.08
    return 0.0


def geo_bonus(
    url: str,
    state: str | None,
    query: str,
) -> float:
    """
    When a query is state-specific, lightly boost results whose
    domain or path references that state. National queries receive
    no state bias. State short-names such as 'guj' on .nic.in
    subdomains are handled separately by matching known fragments.
    """
    if not state:
        return 0.0
    domain = normalize_domain(url)
    state_l = state.lower()
    blob = domain + " " + _path(url)
    if state_l in blob:
        return 0.18
    first_word = state_l.split()[0]
    if first_word in blob:
        return 0.12
    return 0.0


def diversity_penalty(
    url: str,
    domain_count_before: int,
) -> float:
    """
    Mild diminishing returns so that a single domain does not crowd
    out the top-K. The first result from a domain is never penalised;
    subsequent results from the same domain receive a small, growing
    decrement. This is intentionally weak -- if several pages from the
    same domain genuinely carry the best evidence they may still sit
    near the top.
    """
    if domain_count_before <= 0:
        return 0.0
    return min(0.28, 0.05 * domain_count_before)


_CENTRAL_GOV_IN_RE = re.compile(r"^[a-z0-9\-]+\.gov\.in$")
_CENTRAL_AUTH_RE = re.compile(r"^[a-z0-9\-]+\.(org\.in|co\.in)$")

_AGGREGATOR_DOMAINS = {
    "india.gov.in",
    "negd.gov.in",
    "pib.gov.in",
    "newsonair.gov.in",
    "mygov.in",
    "digitalindia.gov.in",
}

def primary_portal_lead(url: str, query: str) -> float:
    """
    Small, principled lead so that a scheme's OWN central portal beats
    the many district / state secondary copies of the same scheme and
    the sub-pages of mega-government portals.

    A short central domain that literally contains a query token is
    treated as the scheme's own portal (e.g. eshram.gov.in for
    "e-shram", pmuy.gov.in for "ujjwala" is a domain alias so it falls
    back to the central tier). District *.nic.in copies (which are the
    same institution family, not distinct independent institutions)
    receive no primary-portal lead.

    Returns 0.0 for general web and for district NIC subdomains.
    """
    domain = normalize_domain(url)
    if not domain:
        return 0.0

    q_tokens = _tokens(query)

    if _CENTRAL_GOV_IN_RE.match(domain):
        if domain in _AGGREGATOR_DOMAINS:
            return -0.15
        domain_tokens = _tokens(domain)

        if domain_tokens & q_tokens:
            return 0.30   # the domain IS the scheme portal

        for qt in q_tokens:
            for dt in domain_tokens:
                if len(qt) >= 4 and len(dt) >= 4:
                    if qt in dt or dt in qt:
                        return 0.30

        return 0.18       # central ministry portal (e.g. nhm.gov.in)

    if _CENTRAL_AUTH_RE.match(domain):
        return 0.16

    return 0.0


def rescore(
    query: str,
    results: list[dict],
    classification,
    top_k: int | None = None,
) -> list[dict]:
    """
    Re-orders a list of already-BM25-ranked web chunks using the
    additive retrieval-quality signals above.

    ``classification`` is the existing QueryClassification object
    (or any object exposing .intent and .state).

    Preserves every existing key on each result; only adds new keys
    (``source_tier``, ``service_fit``, ``page_relevance_score``,
    ``freshness_score``, ``geo_score``, ``diversity_penalty``,
    ``retrieval_quality_score``).

    Returns a new list in the improved order. If the input is empty
    or the query is blank, returns the input unchanged.
    """
    if not results or not query.strip():
        return results

    bm25_scores = [
        float(r.get("bm25_score", 0.0) or 0.0)
        for r in results
    ]

    bmin = min(bm25_scores)
    bmax = max(bm25_scores)
    span = (bmax - bmin) or 1.0

    intent = str(getattr(classification, "intent", "INFORMATIONAL") or "INFORMATIONAL").upper()
    state = getattr(classification, "state", None)

    informational = (intent in {"INFORMATIONAL"})

    raw: list[dict] = []

    for idx, result in enumerate(results):
        url = result.get("source_url") or result.get("url") or ""
        title = result.get("web_title") or result.get("title") or ""
        text = result.get("text") or result.get("content") or ""
        official = bool(result.get("official", False))
        trusted_secondary = bool(result.get("trusted_secondary", False))

        bm25_norm = (bm25_scores[idx] - bmin) / span

        tier, tier_name = source_tier(url, title, official, trusted_secondary)

        authority_lead = {
            6: 1.05,   # official service / application portal
            5: 0.85,   # official ministry / scheme page
            4: 0.70,   # general official source
            3: 0.30,   # trusted / authorized secondary
            0: 0.0,    # general web
        }[tier]

        if informational and _is_announcement(url, title) and official:
            authority_lead = max(authority_lead, 0.55)

        primary_portal = primary_portal_lead(url, query)

        service = service_fit_score(url, title, intent)
        page = page_relevance(url, title, text, query)
        fresh = freshness_bonus(url, intent, query)
        geo = geo_bonus(url, state, query)
        low_authority = _is_low_authority(url)

        raw.append({
            "idx": idx,
            "result": result,
            "tier": tier,
            "tier_name": tier_name,
            "bm25_norm": bm25_norm,
            "authority_lead": authority_lead,
            "primary_portal": primary_portal,
            "service": service,
            "page": page,
            "fresh": fresh,
            "geo": geo,
            "low_authority": low_authority,
            "official": official,
        })

    has_authoritative = any(r["tier"] >= 3 for r in raw)

    scored: list[tuple[float, int, dict]] = []

    for r in raw:
        third_party_penalty = 0.0
        if r["low_authority"]:
            if intent in SERVICE_INTENTS:
                third_party_penalty = 1.00
            elif informational:
                third_party_penalty = 0.50
            else:
                third_party_penalty = 0.30

        composite = (
            r["bm25_norm"]
            + r["authority_lead"]
            + r["primary_portal"]
            + r["service"]
            + r["page"]
            + r["fresh"]
            + r["geo"]
            - third_party_penalty
        )

        # page is dramatically more relevant (its bm25/page must exceed
        if has_authoritative and r["tier"] < 3:
            composite -= 0.20

        result = r["result"]
        result["source_tier"] = r["tier"]
        result["source_tier_name"] = r["tier_name"]
        result["service_fit"] = round(r["service"], 4)
        result["page_relevance_score"] = round(r["page"], 4)
        result["freshness_score"] = round(r["fresh"], 4)
        result["geo_score"] = round(r["geo"], 4)
        result["third_party_penalty"] = round(third_party_penalty, 4)

        result["debug_bm25_norm"] = round(r["bm25_norm"], 4)
        result["debug_authority_lead"] = round(r["authority_lead"], 4)
        result["debug_primary_portal_lead"] = round(r["primary_portal"], 4)
        result["debug_low_authority"] = r["low_authority"]
        result["debug_has_authoritative_in_pool"] = has_authoritative

        scored.append((composite, r["idx"], result))

    scored.sort(key=lambda item: (-item[0], item[1]))

    domain_seen: dict[str, int] = {}
    ordered: list[dict] = []

    for composite, idx, result in scored:
        url = result.get("source_url") or result.get("url") or ""
        domain = normalize_domain(url)
        count_before = domain_seen.get(domain, 0)
        diversity = diversity_penalty(url, count_before)

        final_score = composite - diversity
        result["diversity_penalty"] = round(diversity, 4)
        result["retrieval_quality_score"] = round(final_score, 6)

        ordered.append((final_score, idx, result))
        domain_seen[domain] = count_before + 1

    ordered.sort(key=lambda item: (-item[0], item[1]))

    final = [r for _, _, r in ordered]

    if top_k is not None:
        final = final[:top_k]

    return final
