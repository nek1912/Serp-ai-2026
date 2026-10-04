"""Chat route — HTTP layer delegating to RAGOrchestrator.

Keeps: ChatRequest model, language detection, domain classification,
       context disambiguation, out-of-scope check, grievance workflow,
       translation helpers.

Delegates to RAGOrchestrator for: evidence merging, prompt building,
LLM generation, citation handling, confidence calculation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.config import Settings, get_settings
from app.domains import get_anchor_store
from app.grievance.workflow import GrievanceWorkflow, load_grievance_state
from app.grievance.field_detector import GrievanceFieldDetector
from app.grievance.input_types import get_input_type
from app.grievance.translations import (
    translate_grievance_string,
    translate_field_label,
)
from app.language import detect_query_languages
from app.providers.embeddings import get_embedding_provider
from app.providers.sarvam_translator import SarvamTranslator
from app.providers.translator import AzureTranslator
from app.resolve_response_language import resolve_and_remember
from app.services.rag_orchestrator import RAGOrchestrator
from app.conversation_store import ensure_conversation
from app.session_store import get_history, get_state, save_message, touch_session, trim_messages
from app.speech_text import build_grievance_speech_text, prepare_speech_text, segment_speech
from app.ui import get_abstain_text
from app.web_rag.query_classifier import QueryClassifier, QueryClassification, INTENT_KEYWORDS

logger = logging.getLogger(__name__)

router = APIRouter()

# QueryClassifier intents that unambiguously indicate a guidance/informational
# query.  When QueryClassifier returns one of these, the query is routed to
# normal RAG — is_grievance_query() is NOT consulted, preventing Gemini
# misclassification from overriding a confident guidance intent.
_GUIDANCE_INTENTS: set[str] = {
    "APPLICATION", "ELIGIBILITY", "DOCUMENT_REQUIREMENTS",
    "REGISTRATION", "BENEFIT", "SUBSIDY_AMOUNT", "DEADLINE",
    "CONTACT", "SERVICE_ACCESS", "LOCATION", "COMPARISON",
}


def _should_route_to_grievance(
    classification: QueryClassification,
    query_text: str,
    input_lang: str = "en",
) -> bool:
    """Decide whether a query should enter the GrievanceWorkflow.

    Routing hierarchy (QueryClassifier is authoritative):
      1. domain=="grievance" or intent=="GRIEVANCE" → True  (explicit complaint)
      2. intent in _GUIDANCE_INTENTS               → False (explicit guidance)
         BUT if the raw query also contains a grievance keyword, override → True
      3. Non-English query                          → scan raw keywords, then False
      4. Ambiguous (INFORMATIONAL/STATUS/unknown)   → fall back to is_grievance_query()
    """
    if classification.domain == "grievance" or classification.intent == "GRIEVANCE":
        return True
    if classification.intent in _GUIDANCE_INTENTS:
        # Guidance intent detected, but check if raw query also contains a
        # grievance keyword (multilingual).  When both signals are present,
        # grievance wins — the user explicitly mentioned a complaint.
        _grievance_kws = INTENT_KEYWORDS.get("GRIEVANCE", [])
        if any(kw in query_text.lower() for kw in _grievance_kws):
            return True
        return False
    # Non-English queries: scan raw text against multilingual grievance
    # keywords before giving up.  is_grievance_query() is English-only
    # (regex + English keywords), but INTENT_KEYWORDS["GRIEVANCE"] already
    # includes Indic words (e.g. ફરિયાદ, शिकायत, रिपोर्ट).  If any of
    # those match, route to grievance immediately.  Otherwise fall through
    # to the post-context check which uses translated english_query.
    if input_lang != "en":
        _grievance_kws = INTENT_KEYWORDS.get("GRIEVANCE", [])
        if any(kw in query_text.lower() for kw in _grievance_kws):
            return True
        return False
    # Ambiguous intent — let the grievance detector act as tiebreaker.
    return _get_grievance_workflow().is_grievance_query(query_text)


@dataclass
class _GrievanceResult:
    """Result of grievance workflow processing."""

    text: str
    grievance: dict | None = None
    stage: str | None = None
    draft_summary: dict | None = None
    fields_schema: dict | None = None
    finalized: bool = False


def _normalize_grievance_result(result) -> _GrievanceResult:
    """Coerce a string or _GrievanceResult into _GrievanceResult."""
    if isinstance(result, _GrievanceResult):
        return result
    text = str(result)
    return _GrievanceResult(text=text)


# ── Singleton lazy-init helpers ──────────────────────────────────────────────

_query_classifier: QueryClassifier | None = None
_grievance_workflow: GrievanceWorkflow | None = None
_rag_orchestrator: RAGOrchestrator | None = None


def _get_query_classifier() -> QueryClassifier:
    global _query_classifier
    if _query_classifier is None:
        _query_classifier = QueryClassifier()
    return _query_classifier


def _get_grievance_workflow() -> GrievanceWorkflow:
    global _grievance_workflow
    if _grievance_workflow is None:
        _grievance_workflow = GrievanceWorkflow()
    return _grievance_workflow


def _get_rag_orchestrator(settings: Settings) -> RAGOrchestrator:
    global _rag_orchestrator
    if _rag_orchestrator is None:
        _rag_orchestrator = RAGOrchestrator(settings)
    return _rag_orchestrator


# ── Grievance dispatch ─────────────────────────────────────────────────────


def _has_active_grievance(session_id: str) -> bool:
    """Check whether a non-complete grievance workflow exists for this session."""
    state = load_grievance_state(session_id)
    return state is not None and not state.is_complete


def _process_grievance_message(
    question: str,
    session_id: str,
    input_lang: str = "en",
    settings: Settings | None = None,
) -> _GrievanceResult:
    """Dispatch a message through the English grievance workflow with
    clean language boundaries.

    INPUT BOUNDARY:  Non-English messages are translated to English
    before entering the workflow.  The workflow only ever sees English.

    WORKFLOW:         The known-good English grievance workflow runs
    unchanged — classification, extraction, prompts, completion.

    OUTPUT BOUNDARY:  The English response is translated to the user's
    target language.  URLs, reference numbers, and the citizen's
    original-language description are protected during translation.

    The canonical ``grievance`` dict bypasses translation entirely.
    """
    ensure_conversation(session_id, session_id)

    # ── INPUT BOUNDARY ─────────────────────────────────────────
    original_message = question  # preserve user's exact words
    workflow_input = question
    if input_lang != "en" and settings is not None:
        workflow_input = _translate_to_english(question, input_lang, settings)

    # ── ENGLISH WORKFLOW ────────────────────────────────────────
    workflow = _get_grievance_workflow()
    result = workflow.process_message(
        user_message=workflow_input,
        conversation_id=session_id,
        user_id=session_id,
    )

    # Store the user's original (pre-translation) description in the
    # draft so the canonical dict can expose it.
    if result.draft and result.draft.original_description is None:
        result.draft.original_description = original_message

    response_text = result.response

    # ── OUTPUT BOUNDARY ────────────────────────────────────────
    if input_lang != "en" and settings is not None:
        response_text = _translate_grievance_response_back(
            response_text, input_lang, settings,
        )

    # ── Build canonical structured grievance object ─────────────
    # NOTE: this is attached whenever a draft exists, starting from the
    # very first turn (classification confirmation) — existing tests
    # (test_language_boundary_e2e.py) depend on `grievance` being
    # present and progressively filled in from turn 1, not just at
    # completion. `grievance.submission` is what's only populated once
    # `result.submission_route` is resolved (the actual completing
    # turn) — the frontend uses THAT (not mere `grievance` presence)
    # to decide when to show the single authoritative GrievanceCard
    # instead of the normal follow-up-question prose.
    grievance_dict = None
    draft_summary = None
    fields_schema = None
    grievance_stage = None

    if result.draft:
        portal_name = None
        english_portal_name = None
        portal_url = None
        submission_dept = None
        submission_level = None
        submission_steps = None
        submission_required_documents = None
        submission_estimated_timeline = None
        submission_disclaimer = None
        if result.submission_route:
            # This block builds the DISPLAY submission info (translated for
            # the user's language). The pure-English canonical values are
            # separately preserved in `english_mirror` below (built from
            # `result.submission_route` directly, untranslated) — that is
            # what the "Show English draft" toggle uses. So it is correct
            # and expected for these display values to be localized.
            from app.grievance.models import SubmissionRoute as _SubmissionRoute

            _localized_route = result.submission_route
            if input_lang and input_lang != "en" and result.draft:
                try:
                    _candidate = _get_grievance_workflow().submission_guide.get_submission_route(
                        result.draft, language=input_lang,
                    )
                    # Defensive: only use the re-resolved route if it's a
                    # real SubmissionRoute (not e.g. a MagicMock in tests,
                    # or any other unexpected object) — using an unchecked
                    # object here previously corrupted portal_name/etc.
                    # with garbage values.
                    if isinstance(_candidate, _SubmissionRoute):
                        _localized_route = _candidate
                except Exception:
                    pass  # fall back to workflow's English route

            portal_name = _localized_route.portal_name
            english_portal_name = portal_name
            portal_url = _localized_route.portal_url
            submission_dept = _localized_route.department
            submission_level = _localized_route.level
            submission_steps = _localized_route.steps
            submission_required_documents = _localized_route.required_documents
            submission_estimated_timeline = _localized_route.estimated_timeline
            submission_disclaimer = _localized_route.disclaimer

            # Translate remaining submission metadata via static map + API
            # fallback (portal name/department/level are short labels with
            # static translations; steps/documents/timeline/disclaimer are
            # longer prose translated via the API as needed). portal_url is
            # NEVER translated — it must stay a real URL or null.
            if input_lang and input_lang != "en" and settings is not None:
                portal_name = _localize(portal_name, input_lang, "portal_name", settings)
                submission_dept = _localize(submission_dept, input_lang, "department", settings)
                submission_level = _localize(submission_level, input_lang, "jurisdiction", settings)
                submission_steps = _translate_list(submission_steps, input_lang, settings)
                submission_required_documents = _translate_list(submission_required_documents, input_lang, settings)
                submission_estimated_timeline = _localize(submission_estimated_timeline, input_lang, settings=settings)
                submission_disclaimer = _localize(submission_disclaimer, input_lang, settings=settings)

        # Localized display description: use the citizen's ORIGINAL text
        # as-is (never translate user-entered text).  The canonical
        # English description is only for internal processing; the user
        # sees exactly what they typed.
        localized_description = None
        if input_lang != "en" and settings is not None:
            # Use original_description if available (the user's exact words),
            # otherwise fall back to the canonical description.
            localized_description = (
                result.draft.original_description
                or result.draft.description
            )

        # Build the English mirror: a full, untranslated English copy of
        # the grievance so the frontend can render a complete English
        # preview page when the user's language is non-English.
        english_mirror = None
        if input_lang and input_lang != "en" and result.draft:
            try:
                # Resolve entity aliases for the mirror's location block
                def _mirror_alias(entity_keys: list[str]) -> str | None:
                    for k in entity_keys:
                        e = result.draft.entities.get(k)
                        if e and hasattr(e, "value") and isinstance(e.value, str) and e.value.strip():
                            return e.value.strip()
                    return None

                from app.grievance.models import (
                    _normalize_ward, _normalize_city,
                    _normalize_locality, _normalize_area,
                )
                raw_city = _mirror_alias(["city", "city_name"]) or ""
                raw_locality = _mirror_alias(["locality", "locality_name"]) or ""
                raw_area = _mirror_alias(["area", "area_name"]) or ""
                raw_ward = _mirror_alias(["ward_number", "ward_name"]) or ""
                mirror_city = _normalize_city(raw_city) if isinstance(raw_city, str) else ""
                mirror_locality = _normalize_locality(raw_locality) if isinstance(raw_locality, str) else ""
                mirror_area = _normalize_area(raw_area) if isinstance(raw_area, str) else ""
                mirror_ward = _normalize_ward(raw_ward) if isinstance(raw_ward, str) else ""
                mirror_district = _mirror_alias(["district"])

                # Structured fields for the english mirror
                _MIRROR_LOCATION_KEYS = {
                    "ward_number", "ward_name", "locality", "locality_name",
                    "colony_name", "area", "area_name", "city", "city_name",
                    "district", "district_name", "tehsil", "tehsil_name",
                    "taluk", "taluk_name", "village", "village_name",
                    "block", "block_name", "zone", "sector_name", "location",
                }
                mirror_fields = {
                    k: v.value.strip()
                    for k, v in result.draft.entities.items()
                    if k not in _MIRROR_LOCATION_KEYS
                    and k != "state"
                    and v.value
                    and v.value.strip()
                }

                category_val = result.draft.category.value
                sub_category_val = result.draft.sub_category.value
                department_val = result.draft.department
                jurisdiction_val = result.draft.jurisdiction
                title_val = result.draft.title
                description_val = result.draft.description
                state_val = result.draft.state

                if not all(isinstance(v, str) for v in [category_val, sub_category_val, department_val, jurisdiction_val, title_val, description_val]):
                    raise TypeError("Mock objects detected in draft fields")

                english_mirror = {
                    "category": category_val.replace("_", " ").title(),
                    "sub_category": sub_category_val.replace("_", " ").title(),
                    "department": department_val,
                    "jurisdiction": jurisdiction_val.title(),
                    "title": title_val,
                    "description": description_val,
                    "fields": mirror_fields or None,
                    "location": {
                        "ward_number": mirror_ward or None,
                        "locality": mirror_locality or None,
                        "area": mirror_area or None,
                        "city": mirror_city or None,
                        "district": mirror_district,
                        "state": state_val,
                    },
                    "submission": {
                        "portal_name": result.submission_route.portal_name if result.submission_route else None,
                        "portal_url": result.submission_route.portal_url if result.submission_route else None,
                        "department": result.submission_route.department if result.submission_route else None,
                        "level": result.submission_route.level if result.submission_route else None,
                        "steps": result.submission_route.steps if result.submission_route else [],
                        "required_documents": result.submission_route.required_documents if result.submission_route else [],
                        "estimated_timeline": result.submission_route.estimated_timeline if result.submission_route else None,
                        "disclaimer": result.submission_route.disclaimer if result.submission_route else None,
                    } if result.submission_route else None,
                }
            except (TypeError, AttributeError):
                # Draft entities are MagicMock objects (tests) or otherwise
                # incomplete — skip the English mirror gracefully.
                english_mirror = None

        grievance_dict = result.draft.to_canonical_dict(
            portal_name=english_portal_name,
            portal_url=portal_url,
            submission_department=submission_dept,
            submission_level=submission_level,
            submission_steps=submission_steps,
            submission_required_documents=submission_required_documents,
            submission_estimated_timeline=submission_estimated_timeline,
            submission_disclaimer=submission_disclaimer,
            localized_description=localized_description,
            english_mirror=english_mirror,
        )

        # NOTE: grievance_dict top-level fields (category, sub_category,
        # department, jurisdiction, title) intentionally stay in ENGLISH.
        # Localized versions are in draft_summary and in the per-field
        # submission metadata below.  The canonical JSON contract
        # requires English labels; see test_language_boundary_e2e.

        # ── Build structured grievance metadata for frontend UI ──
        from app.grievance.models import GrievanceStage

        stage = result.stage
        if stage == GrievanceStage.CLASSIFICATION:
            grievance_stage = "classification"
            draft_summary = {
                "category": result.draft.category.value.replace("_", " ").title(),
                "sub_category": result.draft.sub_category.value.replace("_", " ").title(),
                "jurisdiction": result.draft.jurisdiction.title(),
                "title": result.draft.title,
                "description": result.draft.original_description or result.draft.description,
                "department": result.draft.department,
            }
            if input_lang and input_lang != "en" and settings is not None:
                draft_summary["category"] = _localize(draft_summary["category"], input_lang, "category", settings)
                draft_summary["sub_category"] = _localize(draft_summary["sub_category"], input_lang, "subcategory", settings)
                draft_summary["jurisdiction"] = _localize(draft_summary["jurisdiction"], input_lang, "jurisdiction", settings)
                draft_summary["department"] = _localize(draft_summary["department"], input_lang, "department", settings)
                draft_summary["title"] = _localize(draft_summary["title"], input_lang, "title", settings)
        elif stage in (GrievanceStage.ENTITY_EXTRACTION, GrievanceStage.FOLLOWUP, GrievanceStage.MISSING_FIELDS):
            grievance_stage = "fields"
            draft_summary = {
                "category": result.draft.category.value.replace("_", " ").title(),
                "sub_category": result.draft.sub_category.value.replace("_", " ").title(),
                "jurisdiction": result.draft.jurisdiction.title(),
                "title": result.draft.title,
                "description": result.draft.original_description or result.draft.description,
                "department": result.draft.department,
            }
            if input_lang and input_lang != "en" and settings is not None:
                draft_summary["category"] = _localize(draft_summary["category"], input_lang, "category", settings)
                draft_summary["sub_category"] = _localize(draft_summary["sub_category"], input_lang, "subcategory", settings)
                draft_summary["jurisdiction"] = _localize(draft_summary["jurisdiction"], input_lang, "jurisdiction", settings)
                draft_summary["department"] = _localize(draft_summary["department"], input_lang, "department", settings)
                draft_summary["title"] = _localize(draft_summary["title"], input_lang, "title", settings)
            # Build fields schema with input types
            field_detector = GrievanceFieldDetector()
            prompts = field_detector.get_field_prompts(result.draft.sub_category)
            field_labels = field_detector.get_field_labels()
            language = input_lang or "en"
            fields_schema = {
                "mandatory_fields": [
                    {
                        "field": f,
                        "field_label": translate_field_label(f, language) if language != "en" else field_labels.get(f, f.replace("_", " ").title()),
                        "question": _localize(
                            prompts.get(f, f"Please provide {f.replace('_', ' ')}."),
                            language, settings=settings,
                        ),
                        "input_type": get_input_type(f),
                        "mandatory": True,
                        "value": result.draft.entities[f].value if f in result.draft.entities else None,
                    }
                    for f in result.draft.required_fields
                ],
                "optional_fields": [
                    {
                        "field": f,
                        "field_label": translate_field_label(f, language) if language != "en" else field_labels.get(f, f.replace("_", " ").title()),
                        "question": _localize(
                            prompts.get(f, f"Please provide {f.replace('_', ' ')}."),
                            language, settings=settings,
                        ),
                        "input_type": get_input_type(f),
                        "mandatory": False,
                        "value": result.draft.entities[f].value if f in result.draft.entities else None,
                    }
                    for f in result.draft.optional_fields
                ],
            }
        elif stage in (GrievanceStage.DRAFT_READY, GrievanceStage.SUBMISSION_GUIDE, GrievanceStage.COMPLETE):
            grievance_stage = "complete"

    save_message(session_id, "user", question)
    save_message(session_id, "assistant", response_text)
    trim_messages(session_id, keep=50)
    return _GrievanceResult(
        text=response_text,
        grievance=grievance_dict,
        stage=grievance_stage,
        draft_summary=draft_summary,
        fields_schema=fields_schema,
        finalized=(grievance_stage == "complete"),
    )


def _translate_grievance_response_back(
    text: str, target_lang: str, settings: Settings,
) -> str:
    """Back-translate a grievance response while preserving URLs and
    the citizen's original-language description.

    Translation APIs can corrupt, reformat, or strip URLs.  They can
    also re-translate text that is already in the target language,
    producing garbled output.  This function extracts all URLs AND any
    ``original_description`` text before translating, replaces them
    with safe alphabetic-only placeholders (no digits, no punctuation
    that translation APIs modify), translates the remaining English
    prose, then restores the originals.

    Placeholder format: ``GRVURLAEND``, ``GRVURLBEND``, …
    (alphabetic index between GRVURL and END — survives digit
    localization in Gujarati/Hindi/etc.)
    """
    import re

    _URL_RE = re.compile(r"(https?://[^\s\)\]\"'>]+)")
    _PLACEHOLDER_LEAK_RE = re.compile(r"GRVURL[A-Z]+END")

    # Pre-translate known workflow prefixes via static map to avoid
    # API transliteration issues (e.g. Hinglish output).
    from app.grievance.translations import WORKFLOW_PREFIX
    for eng, langs in WORKFLOW_PREFIX.items():
        if eng in text and target_lang in langs:
            text = text.replace(eng, langs[target_lang])

    urls = _URL_RE.findall(text)
    placeholders: dict[str, str] = {}
    protected = text

    # ── 1. Protect URLs ──────────────────────────────────────────
    for i, url in enumerate(urls):
        token = f"GRVURL{chr(65 + i)}END"
        placeholders[token] = url
        protected = protected.replace(url, token, 1)

    # ── 2. Protect original_description (Gujarati/Indic text) ───
    # The formatted draft contains a line like:
    #   **Description:** <original_description>
    # We protect the value after the label so Sarvam doesn't
    # re-translate already-correct Indic text.
    _DESC_LABEL_RE = re.compile(
        r"(\*\*Description:\*\*\s*)(.+?)(?=\n\n|\n\*\*|\Z)",
        re.DOTALL,
    )
    desc_match = _DESC_LABEL_RE.search(protected)
    if desc_match:
        desc_text = desc_match.group(2).strip()
        # Only protect if it contains non-ASCII (Indic script)
        if any(ord(c) > 127 for c in desc_text):
            desc_token = "GRVDESCORIGINAL"
            placeholders[desc_token] = desc_text
            protected = (
                protected[: desc_match.start(2)]
                + desc_token
                + protected[desc_match.end(2) :]
            )

    # ── 3. Translate ─────────────────────────────────────────────
    translated = _translate_from_english(protected, target_lang, settings)

    # ── 4. Restore all protected tokens ──────────────────────────
    for token, original in placeholders.items():
        translated = translated.replace(token, original)

    # ── 5. Defensive: strip any leaked placeholder tokens ────────
    # If translation API split/modified a token, remove it rather
    # than return an internal placeholder to the user.
    translated = _PLACEHOLDER_LEAK_RE.sub("[link]", translated)

    return translated


# ── Request model ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str
    language: Literal["en", "hi", "gu", "mr", "bn", "ta", "te", "kn", "pa", "or", "ml"]
    ui_language_explicit: bool = False
    state: str | None = None
    as_of_date: str | None = None
    history: list[dict] | None = None
    mode: Literal["static", "web", "rag_web"] | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _confidence_level(score: float) -> str:
    if score >= 0.7:
        return "high"
    elif score >= 0.5:
        return "moderate"
    elif score > 0.0:
        return "low"
    return "none"


def _translate_to_english(question: str, input_lang: str, settings: Settings) -> str:
    if input_lang == "en":
        return question
    sarvam = SarvamTranslator(settings)
    if sarvam.configured:
        try:
            return sarvam.translate(
                question,
                to="en",
                source=input_lang,
                translation_stage="input_query",
            )
        except Exception:
            logger.warning("Sarvam translation failed, trying Azure")
    try:
        return AzureTranslator(settings).translate(question, to="en", source=input_lang)
    except Exception:
        logger.warning("Azure translation failed, using original")
        return question


def _translate_from_english(text: str, target_lang: str, settings: Settings) -> str:
    """Translate English text to target_lang via Sarvam → Azure fallback.

    Returns the translated text, or the ORIGINAL English text if ALL
    providers fail.  The caller MUST check whether the result actually
    changed — a returned English string means translation failed, not
    that the text was already in the target language.
    """
    if target_lang == "en":
        return text
    text = str(text)
    translation_start = time.monotonic()
    citation_tokens: dict[str, str] = {}
    protected_text = text
    for index, marker in enumerate(re.findall(r"\[(?:chunk|web_)[^\]]+\]", text)):
        token = f"TRANSLATIONCITATION{index}END"
        citation_tokens[token] = marker
        protected_text = protected_text.replace(marker, token, 1)
    sarvam = SarvamTranslator(settings)
    if sarvam.configured:
        try:
            translated = sarvam.translate(
                protected_text,
                to=target_lang,
                source="en",
                translation_stage="final_answer",
            )
            if translated != protected_text:
                translated = _restore_translation_tokens(translated, citation_tokens)
                logger.info(
                    "translation_stage=final_answer final_translation_ms=%.0f",
                    (time.monotonic() - translation_start) * 1000,
                )
                return translated
        except Exception:
            logger.warning("Sarvam back-translation failed")
    try:
        translated = AzureTranslator(settings).translate(protected_text, to=target_lang, source="en")
        if translated != protected_text:
            return _restore_translation_tokens(translated, citation_tokens)
    except Exception:
        logger.warning("Azure back-translation failed")
    logger.warning("All translation providers failed for '%s' → %s", target_lang, text[:80])
    return text


def _restore_translation_tokens(text: str, tokens: dict[str, str]) -> str:
    for token, original in tokens.items():
        text = text.replace(token, original)
    return text


def _localize(english: str, lang: str, map_name: str | None = None, settings: Settings | None = None) -> str:
    """Translate a grievance string using the static map first, then API.

    This is the SINGLE translation entry point for all system-generated
    grievance content.  The static map guarantees deterministic, correct
    translations for known strings.  The API is only used as a secondary
    enhancement for dynamic/free-form text not in the map.

    IMPORTANT: When a string is NOT found in the static map AND the API
    translation is unavailable or fails, this function MUST NOT silently
    return English for a non-English target language.  Instead it logs a
    warning so the missing translation can be identified and fixed.
    """
    if lang == "en":
        return english

    # 1. Try static map (guaranteed, offline, fast)
    result = translate_grievance_string(english, lang, map_name=map_name)
    if result != english:
        return result

    # 2. Fallback: API translation (for dynamic text not in the map)
    if settings is not None:
        translated = _translate_from_english(english, lang, settings)
        if translated != english:
            return translated
        # API returned English — this is a leak; log it
        logger.warning(
            "TRANSLATION LEAK: no static or API translation for '%s' → %s",
            lang, english[:80],
        )

    logger.warning(
        "TRANSLATION LEAK: no translation for '%s' (lang=%s, map=%s)",
        english[:80], lang, map_name,
    )
    return english


def _translate_list(items: list[str] | None, target_lang: str, settings: Settings) -> list[str] | None:
    """Translate a list of English strings using static map + API fallback."""
    if not items or target_lang == "en":
        return items
    return [_localize(item, target_lang, settings=settings) for item in items]


def _abstain(lang: str, session_id: str | None = None) -> dict:
    answer = get_abstain_text(lang)
    return {
        "answer": answer, "language": lang, "domain": "unknown",
        "intent": "unknown", "entities": [],
        "confidence": 0.0, "confidence_level": "none",
        "citations": [], "abstained": True,
        "speech_text": prepare_speech_text(answer),
        "speech_segments": [],
        "follow_up_question": None,
        "mode": "dual_rag", "conversation_id": session_id or "",
    }


def _rag_response_to_dict(resp, lang: str, session_id: str) -> dict:
    return {
        "answer": resp.answer,
        "language": lang,
        "domain": resp.domain,
        "intent": resp.domain,
        "entities": [],
        "confidence": resp.confidence,
        "confidence_level": _confidence_level(resp.confidence),
        "citations": resp.citations,
        "abstained": resp.abstained,
        "speech_text": resp.speech_text,
        "speech_segments": resp.speech_segments,
        "follow_up_question": resp.follow_up_question,
        "mode": resp.mode,
        "conversation_id": session_id,
    }


@dataclass
class _ChatContext:
    """Resolved context shared between /chat and /chat/stream."""
    settings: Settings
    lang: str
    input_lang: str
    english_query: str
    embedding: list[float]
    domain: str
    classification: QueryClassification
    history: list[dict] | None
    resolved_state: str | None
    language_mix: dict[str, float] | None = None


@lru_cache(maxsize=1000)
def _cached_embedding(query_hash: str, query: str):
    return get_embedding_provider().embed_texts([query], task="retrieval.query")[0]

@lru_cache(maxsize=500)
def _cached_classification(query_lower: str):
    return _get_query_classifier().classify(query_lower)

async def _resolve_context(req: ChatRequest) -> _ChatContext:
    """Detect language, translate, classify domain, disambiguate context."""
    settings = get_settings()
    ui_code = req.language if req.ui_language_explicit else None
    lang = resolve_and_remember(req.session_id, req.question, ui_code)
    detected = detect_query_languages(req.question)
    input_lang = detected.get("dominant") or "en"
    language_mix = detected.get("language_mix")

    async def get_embedding():
        return await asyncio.to_thread(_cached_embedding, req.question, req.question)

    async def get_translation():
        if input_lang != "en":
            return await asyncio.to_thread(_translate_to_english, req.question, input_lang, settings)
        return req.question

    # Run embedding and translation in parallel
    embedding, english_query = await asyncio.gather(
        get_embedding(), get_translation()
    )

    # Classify on the translated English query for robust keyword matching
    classification = await asyncio.to_thread(_cached_classification, english_query.lower())

    history = req.history if req.history is not None else get_history(req.session_id, limit=8)
    
    # We use english_query for domain classification fallback inside anchor store
    domain, _score = await asyncio.to_thread(get_anchor_store().classify, english_query, embedding)

    # AnchorStore context disambiguation
    rules = getattr(get_anchor_store(), "rules", {})
    has_explicit_keyword = False
    if isinstance(rules, dict):
        has_explicit_keyword = any(
            any(kw in english_query.lower() for kw in kws)
            for kws in rules.values() if isinstance(kws, (list, set, tuple))
        )

    if (not has_explicit_keyword or domain == "out_of_scope") and history:
        user_turns = [h["content"] for h in history if isinstance(h, dict) and h.get("role") == "user" and h.get("content")]
        if user_turns:
            anchor_q = None
            if isinstance(rules, dict):
                for prev_q in reversed(user_turns):
                    has_kw = any(
                        any(kw in prev_q.lower() for kw in kws)
                        for kws in rules.values() if isinstance(kws, (list, set, tuple))
                    )
                    if has_kw:
                        anchor_q = prev_q
                        break
            if not anchor_q:
                anchor_q = user_turns[-1]

            contextual_query = f"{anchor_q} {english_query}"
            ctx_embedding = get_embedding_provider().embed_texts([contextual_query], task="retrieval.query")[0]
            ctx_domain, _ctx_score = get_anchor_store().classify(contextual_query, ctx_embedding)
            if ctx_domain != "out_of_scope":
                domain = ctx_domain
                english_query = contextual_query
                embedding = ctx_embedding

    resolved_state = req.state if req.state is not None else get_state(req.session_id)

    return _ChatContext(
        settings=settings,
        lang=lang,
        input_lang=input_lang,
        english_query=english_query,
        embedding=embedding,
        domain=domain,
        classification=classification,
        history=history,
        resolved_state=resolved_state,
        language_mix=language_mix,
    )


# ── Main chat endpoint ──────────────────────────────────────────────────────


@router.post("/chat")
async def chat(req: ChatRequest, user_id: str = Depends(require_auth)) -> dict:
    question = req.question.strip()
    if not question:
        return _abstain(req.language, session_id=req.session_id)

    try:
        # Resolve language early so the grievance path can translate input/output.
        settings = get_settings()
        detected_lang = resolve_and_remember(
            req.session_id, req.question,
            req.language if req.ui_language_explicit else None,
        )

        # ── Active grievance workflow takes priority over fresh classification ──
        if _has_active_grievance(req.session_id):
            grievance_result = _normalize_grievance_result(
                _process_grievance_message(
                    req.question, req.session_id,
                    input_lang=detected_lang, settings=settings,
                )
            )
            resp = {
                "answer": grievance_result.text,
                "language": detected_lang,
                "domain": "grievance",
                "intent": "GRIEVANCE",
                "entities": [],
                "confidence": 1.0,
                "confidence_level": "high",
                "citations": [],
                "abstained": False,
                "speech_text": "",
                "speech_segments": [],
                "follow_up_question": None,
                "mode": "grievance",
                "conversation_id": req.session_id,
            }
            if grievance_result.grievance:
                _speech_src = build_grievance_speech_text(
                    grievance_result.grievance, detected_lang,
                    grievance_result.stage, grievance_result.fields_schema,
                )
                resp["speech_text"] = _speech_src
                resp["speech_segments"] = segment_speech(_speech_src, detected_lang)
                resp["grievance"] = grievance_result.grievance
            else:
                resp["speech_text"] = prepare_speech_text(grievance_result.text)
                resp["speech_segments"] = segment_speech(grievance_result.text, detected_lang)
            if grievance_result.stage:
                resp["grievance_stage"] = grievance_result.stage
            if grievance_result.draft_summary:
                resp["grievance_draft_summary"] = grievance_result.draft_summary
            if grievance_result.fields_schema:
                resp["grievance_fields_schema"] = grievance_result.fields_schema
            if grievance_result.finalized:
                resp["grievance_finalized"] = True
            return resp

        # ── Early grievance detection (skip embedding/translation) ──
        # Run lightweight classification on the raw question (lower‑cased) to see if it is a grievance.
        # This avoids external embedding/translation calls which require API keys and network access.
        raw_classification = _cached_classification(req.question.lower())
        _input_lang_early = (detect_query_languages(req.question).get("dominant") or "en")
        if _should_route_to_grievance(raw_classification, req.question, input_lang=_input_lang_early):
            # Process via the full grievance workflow, preserving all response fields.
            grievance_result = _normalize_grievance_result(
                _process_grievance_message(
                    req.question, req.session_id,
                    input_lang=detected_lang, settings=settings,
                )
            )
            resp = {
                "answer": grievance_result.text,
                "language": detected_lang,
                "domain": "grievance",
                "intent": "GRIEVANCE",
                "entities": [],
                "confidence": raw_classification.confidence,
                "confidence_level": _confidence_level(raw_classification.confidence),
                "citations": [],
                "abstained": False,
                "speech_text": "",
                "speech_segments": [],
                "follow_up_question": None,
                "mode": "grievance",
                "conversation_id": req.session_id,
            }
            if grievance_result.grievance:
                _speech_src2 = build_grievance_speech_text(
                    grievance_result.grievance, detected_lang,
                    grievance_result.stage, grievance_result.fields_schema,
                )
                resp["speech_text"] = _speech_src2
                resp["speech_segments"] = segment_speech(_speech_src2, detected_lang)
                resp["grievance"] = grievance_result.grievance
            else:
                resp["speech_text"] = prepare_speech_text(grievance_result.text)
                resp["speech_segments"] = segment_speech(grievance_result.text, detected_lang)
            if grievance_result.stage:
                resp["grievance_stage"] = grievance_result.stage
            if grievance_result.draft_summary:
                resp["grievance_draft_summary"] = grievance_result.draft_summary
            if grievance_result.fields_schema:
                resp["grievance_fields_schema"] = grievance_result.fields_schema
            if grievance_result.finalized:
                resp["grievance_finalized"] = True
            return resp
        # If not a grievance, proceed with normal context resolution.
        ctx = await _resolve_context(req)

        # Grievance queries → dedicated workflow (fallback after context)
        if _should_route_to_grievance(ctx.classification, ctx.english_query, input_lang=ctx.lang):
            grievance_result = _normalize_grievance_result(
                _process_grievance_message(
                    req.question, req.session_id,
                    input_lang=ctx.lang, settings=ctx.settings,
                )
            )
            resp = {
                "answer": grievance_result.text,
                "language": ctx.lang,
                "domain": "grievance",
                "intent": "GRIEVANCE",
                "entities": [],
                "confidence": ctx.classification.confidence,
                "confidence_level": _confidence_level(ctx.classification.confidence),
                "citations": [],
                "abstained": False,
                "speech_text": "",
                "speech_segments": [],
                "follow_up_question": None,
                "mode": "grievance",
                "conversation_id": req.session_id,
            }
            if grievance_result.grievance:
                _speech_src3 = build_grievance_speech_text(
                    grievance_result.grievance, ctx.lang,
                    grievance_result.stage, grievance_result.fields_schema,
                )
                resp["speech_text"] = _speech_src3
                resp["speech_segments"] = segment_speech(_speech_src3, ctx.lang)
                resp["grievance"] = grievance_result.grievance
            else:
                resp["speech_text"] = prepare_speech_text(grievance_result.text)
                resp["speech_segments"] = segment_speech(grievance_result.text, ctx.lang)
            if grievance_result.stage:
                resp["grievance_stage"] = grievance_result.stage
            if grievance_result.draft_summary:
                resp["grievance_draft_summary"] = grievance_result.draft_summary
            if grievance_result.fields_schema:
                resp["grievance_fields_schema"] = grievance_result.fields_schema
            if grievance_result.finalized:
                resp["grievance_finalized"] = True
            return resp

        touch_session(req.session_id, ctx.resolved_state, ctx.lang)

        # Out-of-scope → abstain (no RAG needed)
        if ctx.domain == "out_of_scope":
            abstain_msg = "I am a cooperative governance assistant and can only answer questions related to cooperatives, agriculture schemes, financial inclusion, and legal provisions in India. Please ask a question within my scope."
            save_message(req.session_id, "user", req.question)
            save_message(req.session_id, "assistant", abstain_msg)
            trim_messages(req.session_id, keep=50)
            return {
                "answer": abstain_msg, "language": ctx.lang, "domain": "out_of_scope",
                "intent": "general", "entities": [],
                "confidence": 0.0, "confidence_level": "none",
                "citations": [], "abstained": True,
                "speech_text": prepare_speech_text(abstain_msg),
                "speech_segments": segment_speech(abstain_msg, ctx.lang),
                "follow_up_question": None,
                "mode": "dual_rag", "conversation_id": req.session_id,
            }

        # ── Core RAG via orchestrator ────────────────────────────────────
        orchestrator = _get_rag_orchestrator(ctx.settings)
        rag_response = await orchestrator.run(
            query=req.question,
            english_query=ctx.english_query,
            embedding=ctx.embedding,
            domain=ctx.domain,
            state=ctx.resolved_state,
            classification=ctx.classification,
            history=ctx.history,
            lang=ctx.lang,
            session_id=req.session_id,
            language_mix=ctx.language_mix,
            pipeline_mode=req.mode,
        )

        # The LLM is instructed to respond in the user's language directly.
        # This translation call is a secondary safety net for any edge case
        # where the LLM does not fully comply with the language instruction.
        if ctx.lang != "en":
            rag_response.answer = _translate_from_english(rag_response.answer, ctx.lang, ctx.settings)
            rag_response.speech_text = prepare_speech_text(rag_response.answer)
            rag_response.speech_segments = segment_speech(rag_response.answer, ctx.lang)

        # ── Session persistence ──────────────────────────────────────────
        save_message(req.session_id, "user", req.question)
        save_message(req.session_id, "assistant", rag_response.answer)
        trim_messages(req.session_id, keep=50)

        return _rag_response_to_dict(rag_response, ctx.lang, req.session_id)

    except Exception:
        logger.exception("Chat route failed")
        return _abstain(req.language, session_id=req.session_id)


# ── SSE streaming endpoint ──────────────────────────────────────────────────

_THINKING_MESSAGES = {
    "en": ["Searching official documents & web...", "Analyzing evidence from both sources...", "Preparing answer..."],
    "hi": ["आधिकारिक दस्तावेज़ और वेब खोज रहे हैं...", "दोनों स्रोतों से साक्ष्य का विश्लेषण...", "उत्तर तैयार कर रहे हैं..."],
    "gu": ["અધિકૃત દસ્તાવેજો અને વેબ શોધી રહ્યા છીએ...", "બંને સ્રોતોમાંથી પુરાવાનું વિશ્લેષણ...", "જવાબ તૈયાર કરી રહ્યા છીએ..."],
    "mr": ["अधिकृत दस्तावेज आणि वेब शोधत आहोत...", "दोन्ही स्रोतांमधून पुराव्याचे विश्लेषण...", "उत्तर तयार करत आहोत..."],
    "bn": ["সরকারি নথিপত্র এবং ওয়েব খুঁজছি...", "উভয় উৎস থেকে প্রমাণ বিশ্লেষণ...", "উত্তর প্রস্তুত করছি..."],
    "ta": ["அதிகாரப்பூர்வ ஆவணங்கள் மற்றும் வலைத்தளத்தை தேடுகிறோம்...", "இரண்டு மூலங்களிலிருந்தும் சான்றுகளை பகுப்பாய்வு செய்கிறோம்...", "பதிலை தயாரிக்கிறோம்..."],
    "te": ["అధికారిక పత్రాలు మరియు వెబ్‌ను శోధిస్తోంది...", "రెండు మూలాల నుండి సాక్ష్యాలను విశ్లేషిస్తోంది...", "సమాధానం తయారు చేస్తోంది..."],
    "kn": ["ಅಧಿಕೃತ ದಸ್ತಾವೇಜುಗಳು ಮತ್ತು ವೆಬ್ ಹುಡುಕುತ್ತಿದೆ...", "ಎರಡೂ ಮೂಲಗಳಿಂದ ಸಾಕ್ಷ್ಯಗಳನ್ನು ವಿಶ್ಲೇಷಿಸುತ್ತಿದೆ...", "ಉತ್ತರ ತಯಾರಿಸುತ್ತಿದೆ..."],
    "pa": ["ਸਰਕਾਰੀ ਦਸਤਾਵੇਜ਼ ਅਤੇ ਵੈੱਬ ਖੋਜ ਰਹੇ ਹਾਂ...", "ਦੋਵਾਂ ਸਰੋਤਾਂ ਤੋਂ ਸਬੂਤਾਂ ਦਾ ਵਿਸ਼ਲੇਸ਼ਣ...", "ਜਵਾਬ ਤਿਆਰ ਕਰ ਰਹੇ ਹਾਂ..."],
    "or": ["ଅଧିକାରିକ ଦସ୍ତାବିଜ ଏବଂ ୱେବ୍ ଖୋଜୁଛି...", "ଉଭୟ ଉତ୍ସରୁ ପ୍ରମାଣ ବିଶ୍ଳେଷଣ...", "ଉତ୍ତର ପ୍ରସ୍ତୁତ କରୁଛି..."],
    "ml": ["�ദ്യോഗിക രേഖകളും വെബും തിരയുന്നു...", "രണ്ട് ഉറവിടങ്ങളിൽ നിന്നുള്ള തെളിവുകൾ വിശകലനം ചെയ്യുന്നു...", "ഉത്തരം തയ്യാറാക്കുന്നു..."],
}

_STEP_LABELS = {
    "en": {
        "retrieval_start": "Searching sources",
        "static_done": "Document search complete",
        "web_done": "Web search complete",
        "evidence_merge": "Merging evidence",
        "llm_generate": "Generating response",
        "citation_verify": "Verifying citations",
    },
    "hi": {
        "retrieval_start": "स्रोत खोज रहे हैं",
        "static_done": "दस्तावेज़ खोज पूर्ण",
        "web_done": "वेब खोज पूर्ण",
        "evidence_merge": "साक्ष्य मर्ज कर रहे हैं",
        "llm_generate": "उत्तर तैयार कर रहे हैं",
        "citation_verify": "उद्धरण सत्यापित कर रहे हैं",
    },
    "gu": {
        "retrieval_start": "સ્ત્રોતો શોધી રહ્યા છીએ",
        "static_done": "દસ્તાવેજ શોધ પૂર્ણ",
        "web_done": "વેબ શોધ પૂર્ણ",
        "evidence_merge": "પુરાવા મર્જ કરી રહ્યા છીએ",
        "llm_generate": "જવાબ તૈયાર કરી રહ્યા છીએ",
        "citation_verify": "સંદર્ભો ચકાસી રહ્યા છીએ",
    },
    "mr": {
        "retrieval_start": "स्रोत शोधत आहोत",
        "static_done": "दस्तावेज शोध पूर्ण",
        "web_done": "वेब शोध पूर्ण",
        "evidence_merge": "पुरावे मर्ज करत आहोत",
        "llm_generate": "उत्तर तयार करत आहोत",
        "citation_verify": "संदर्भ तपासत आहोत",
    },
    "bn": {
        "retrieval_start": "উৎস খুঁজছি",
        "static_done": "নথি অনুসন্ধান সম্পূর্ণ",
        "web_done": "ওয়েব অনুসন্ধান সম্পূর্ণ",
        "evidence_merge": "প্রমাণ মার্জ করছি",
        "llm_generate": "উত্তর তৈরি করছি",
        "citation_verify": "উদ্ধৃতি যাচাই করছি",
    },
    "ta": {
        "retrieval_start": "ஆதாரங்களை தேடுகிறோம்",
        "static_done": "ஆவண தேடல் நிறைவடைந்தது",
        "web_done": "வலை தேடல் நிறைவடைந்தது",
        "evidence_merge": "சான்றுகளை இணைக்கிறோம்",
        "llm_generate": "பதிலை உருவாக்குகிறோம்",
        "citation_verify": "மேற்கோள்களை சரிபார்க்கிறோம்",
    },
    "te": {
        "retrieval_start": "మూలాలను శోధిస్తోంది",
        "static_done": "పత్ర శోధన పూర్తయింది",
        "web_done": "వెబ్ శోధన పూర్తయింది",
        "evidence_merge": "సాక్ష్యాలను మిళితం చేస్తోంది",
        "llm_generate": "సమాధానం రూపొందిస్తోంది",
        "citation_verify": "ఉల్లేఖనాలను ధృవీకరిస్తోంది",
    },
    "kn": {
        "retrieval_start": "ಮೂಲಗಳನ್ನು ಹುಡುಕುತ್ತಿದೆ",
        "static_done": "ದಸ್ತಾವೇಜು ಹುಡುಕಾಟ ಪೂರ್ಣಗೊಂಡಿದೆ",
        "web_done": "ವೆಬ್ ಹುಡುಕಾಟ ಪೂರ್ಣಗೊಂಡಿದೆ",
        "evidence_merge": "ಸಾಕ್ಷ್ಯಗಳನ್ನು ಮಿಶ್ರಣ ಮಾಡುತ್ತಿದೆ",
        "llm_generate": "ಉತ್ತರವನ್ನು ರಚಿಸುತ್ತಿದೆ",
        "citation_verify": "ಉಲ್ಲೇಖಗಳನ್ನು ಪರಿಶೀಲಿಸುತ್ತಿದೆ",
    },
    "pa": {
        "retrieval_start": "ਸਰੋਤ ਖੋਜ ਰਹੇ ਹਾਂ",
        "static_done": "ਦਸਤਾਵੇਜ਼ ਖੋਜ ਪੂਰੀ",
        "web_done": "ਵੈੱਬ ਖੋਜ ਪੂਰੀ",
        "evidence_merge": "ਸਬੂਤ ਮਿਲਾ ਰਹੇ ਹਾਂ",
        "llm_generate": "ਜਵਾਬ ਤਿਆਰ ਕਰ ਰਹੇ ਹਾਂ",
        "citation_verify": "ਹਵਾਲੇ ਜਾਂਚ ਰਹੇ ਹਾਂ",
    },
    "or": {
        "retrieval_start": "ଉତ୍ସ ଖୋଜୁଛି",
        "static_done": "ଦସ୍ତାବିଜ ଖୋଜ ସମ୍ପୂର୍ଣ୍ଣ",
        "web_done": "ୱେବ୍ ଖୋଜ ସମ୍ପୂର୍ଣ୍ଣ",
        "evidence_merge": "ପ୍ରମାଣ ମିଶାଉଛି",
        "llm_generate": "ଉତ୍ତର ତିଆରି କରୁଛି",
        "citation_verify": "ହୱାଲା ଯାଞ୍ଚ କରୁଛି",
    },
    "ml": {
        "retrieval_start": "ഉറവിടങ്ങൾ തിരയുന്നു",
        "static_done": "രേഖ തേടൽ പൂർത്തിയായി",
        "web_done": "വെബ് തേടൽ പൂർത്തിയായി",
        "evidence_merge": "തെളിവുകൾ ലയിപ്പിക്കുന്നു",
        "llm_generate": "ഉത്തരം തയ്യാറാക്കുന്നു",
        "citation_verify": "ഉദ്ധരണികൾ പരിശോധിക്കുന്നു",
    },
}


def _make_step_emitter():
    """Return a sync callback and an async generator for real-time step events.

    The callback is thread-safe (uses queue.Queue) so it can be called from
    inside orchestrator.run(). The async generator yields step dicts as they
    arrive, with a short timeout to avoid blocking.
    """
    q: queue.Queue[dict | None] = queue.Queue()

    def _collect(step_data: dict) -> None:
        q.put(step_data)

    async def _drain():
        while True:
            try:
                item = q.get(timeout=0.1)
                if item is None:
                    break
                yield item
            except queue.Empty:
                continue

    return _collect, _drain()


def _sse_event(event: str, data: dict | str) -> str:
    payload = json.dumps(data, default=str) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(require_auth)):
    """Streaming version — delegates to orchestrator, emits SSE events."""

    async def generate():
        try:
            # Fire first thinking event IMMEDIATELY so user sees feedback
            initial_lang = req.language or "en"
            initial_msgs = _THINKING_MESSAGES.get(initial_lang, _THINKING_MESSAGES["en"])
            yield _sse_event("thinking", {"text": initial_msgs[0]})

            # ── Active grievance workflow takes priority over fresh classification ──
            if _has_active_grievance(req.session_id):
                _settings = get_settings()
                _detected_lang = resolve_and_remember(
                    req.session_id, req.question,
                    req.language if req.ui_language_explicit else None,
                )
                grievance_result = _normalize_grievance_result(
                    _process_grievance_message(
                        req.question, req.session_id,
                        input_lang=_detected_lang, settings=_settings,
                    )
                )
                meta = {
                    "domain": "grievance", "confidence": 1.0,
                    "confidence_level": "high", "mode": "grievance",
                    "citations": [], "abstained": False, "language": _detected_lang,
                    "conversation_id": req.session_id,
                }
                if grievance_result.grievance:
                    meta["grievance"] = grievance_result.grievance
                    _speech_src = build_grievance_speech_text(
                        grievance_result.grievance, _detected_lang,
                        grievance_result.stage, grievance_result.fields_schema,
                    )
                    meta["speech_text"] = _speech_src
                    meta["speech_segments"] = segment_speech(_speech_src, _detected_lang)
                else:
                    meta["speech_text"] = prepare_speech_text(grievance_result.text)
                    meta["speech_segments"] = segment_speech(grievance_result.text, _detected_lang)
                if grievance_result.stage:
                    meta["grievance_stage"] = grievance_result.stage
                if grievance_result.draft_summary:
                    meta["grievance_draft_summary"] = grievance_result.draft_summary
                if grievance_result.fields_schema:
                    meta["grievance_fields_schema"] = grievance_result.fields_schema
                if grievance_result.finalized:
                    meta["grievance_finalized"] = True
                yield _sse_event("metadata", meta)
                for token in grievance_result.text.split(" "):
                    yield _sse_event("token", {"text": token + " "})
                yield _sse_event("done", {})
                return

            # ── Early grievance detection (skip embedding/translation) ──
            # Mirrors the /chat endpoint: classify the raw question with the
            # lightweight, purely-local keyword classifier BEFORE doing any
            # network-dependent work (embedding provider, translation API).
            # Without this, a genuine grievance query would only be detected
            # *after* _resolve_context() ran — and if the embedding/translation
            # call failed or errored for any reason (rate limit, timeout,
            # network hiccup), the whole request fell into the generic
            # `except Exception` handler below and returned the abstention
            # message ("This answer needs human verification") instead of
            # ever reaching the GrievanceWorkflow.
            _settings2 = get_settings()
            _detected_lang2 = resolve_and_remember(
                req.session_id, req.question,
                req.language if req.ui_language_explicit else None,
            )
            raw_classification = _cached_classification(req.question.lower())
            _input_lang_stream_early = (detect_query_languages(req.question).get("dominant") or "en")
            if _should_route_to_grievance(raw_classification, req.question, input_lang=_input_lang_stream_early):
                thinking_msgs = _THINKING_MESSAGES.get(_detected_lang2, _THINKING_MESSAGES["en"])
                yield _sse_event("thinking", {"text": thinking_msgs[0]})
                grievance_result = _normalize_grievance_result(
                    _process_grievance_message(
                        req.question, req.session_id,
                        input_lang=_detected_lang2, settings=_settings2,
                    )
                )
                meta = {
                    "domain": "grievance", "confidence": raw_classification.confidence,
                    "confidence_level": _confidence_level(raw_classification.confidence),
                    "mode": "grievance",
                    "citations": [], "abstained": False, "language": _detected_lang2,
                    "conversation_id": req.session_id,
                }
                if grievance_result.grievance:
                    meta["grievance"] = grievance_result.grievance
                    _speech_src2 = build_grievance_speech_text(
                        grievance_result.grievance, _detected_lang2,
                        grievance_result.stage, grievance_result.fields_schema,
                    )
                    meta["speech_text"] = _speech_src2
                    meta["speech_segments"] = segment_speech(_speech_src2, _detected_lang2)
                else:
                    meta["speech_text"] = prepare_speech_text(grievance_result.text)
                    meta["speech_segments"] = segment_speech(grievance_result.text, _detected_lang2)
                if grievance_result.stage:
                    meta["grievance_stage"] = grievance_result.stage
                if grievance_result.draft_summary:
                    meta["grievance_draft_summary"] = grievance_result.draft_summary
                if grievance_result.fields_schema:
                    meta["grievance_fields_schema"] = grievance_result.fields_schema
                if grievance_result.finalized:
                    meta["grievance_finalized"] = True
                yield _sse_event("metadata", meta)
                for token in grievance_result.text.split(" "):
                    yield _sse_event("token", {"text": token + " "})
                yield _sse_event("done", {})
                return

            ctx = await _resolve_context(req)
            thinking_msgs = _THINKING_MESSAGES.get(ctx.lang, _THINKING_MESSAGES["en"])

            # Grievance → dedicated workflow (fallback after context, in case
            # contextual disambiguation in _resolve_context changes the query)
            if _should_route_to_grievance(ctx.classification, ctx.english_query, input_lang=ctx.lang):
                yield _sse_event("thinking", {"text": thinking_msgs[0]})
                grievance_result = _normalize_grievance_result(
                    _process_grievance_message(
                        req.question, req.session_id,
                        input_lang=ctx.lang, settings=ctx.settings,
                    )
                )
                meta = {
                    "domain": "grievance", "confidence": ctx.classification.confidence,
                    "confidence_level": _confidence_level(ctx.classification.confidence),
                    "mode": "grievance",
                    "citations": [], "abstained": False, "language": ctx.lang,
                    "conversation_id": req.session_id,
                }
                if grievance_result.grievance:
                    meta["grievance"] = grievance_result.grievance
                    _speech_src3 = build_grievance_speech_text(
                        grievance_result.grievance, ctx.lang,
                        grievance_result.stage, grievance_result.fields_schema,
                    )
                    meta["speech_text"] = _speech_src3
                    meta["speech_segments"] = segment_speech(_speech_src3, ctx.lang)
                else:
                    meta["speech_text"] = prepare_speech_text(grievance_result.text)
                    meta["speech_segments"] = segment_speech(grievance_result.text, ctx.lang)
                if grievance_result.stage:
                    meta["grievance_stage"] = grievance_result.stage
                if grievance_result.draft_summary:
                    meta["grievance_draft_summary"] = grievance_result.draft_summary
                if grievance_result.fields_schema:
                    meta["grievance_fields_schema"] = grievance_result.fields_schema
                if grievance_result.finalized:
                    meta["grievance_finalized"] = True
                yield _sse_event("metadata", meta)
                for token in grievance_result.text.split(" "):
                    yield _sse_event("token", {"text": token + " "})
                yield _sse_event("done", {})
                return

            touch_session(req.session_id, ctx.resolved_state, ctx.lang)

            # Out-of-scope → abstain
            if ctx.domain == "out_of_scope":
                yield _sse_event("thinking", {"text": thinking_msgs[1]})
                abstain_msg = "I am a cooperative governance assistant and can only answer questions related to cooperatives, agriculture schemes, financial inclusion, and legal provisions in India. Please ask a question within my scope."
                save_message(req.session_id, "user", req.question)
                save_message(req.session_id, "assistant", abstain_msg)
                trim_messages(req.session_id, keep=50)
                yield _sse_event("metadata", {
                    "domain": "out_of_scope", "confidence": 0.0,
                    "confidence_level": "none", "citations": [],
                    "abstained": True, "language": ctx.lang,
                })
                for token in abstain_msg.split(" "):
                    yield _sse_event("token", {"text": token + " "})
                yield _sse_event("done", {})
                return

            # ── Core RAG via orchestrator ────────────────────────────────
            orchestrator = _get_rag_orchestrator(ctx.settings)
            rag_response = await orchestrator.run(
                query=req.question,
                english_query=ctx.english_query,
                embedding=ctx.embedding,
                domain=ctx.domain,
                state=ctx.resolved_state,
                classification=ctx.classification,
                history=ctx.history,
                lang=ctx.lang,
                session_id=req.session_id,
                language_mix=ctx.language_mix,
                pipeline_mode=req.mode,
            )

            # Keep the final language conversion at one explicit response boundary.
            if ctx.lang != "en":
                rag_response.answer = _translate_from_english(rag_response.answer, ctx.lang, ctx.settings)
                rag_response.speech_text = prepare_speech_text(rag_response.answer)
                rag_response.speech_segments = segment_speech(rag_response.answer, ctx.lang)

            # Emit tokens
            for token in rag_response.answer.split(" "):
                yield _sse_event("token", {"text": token + " "})

            # Session persistence
            save_message(req.session_id, "user", req.question)
            save_message(req.session_id, "assistant", rag_response.answer)
            trim_messages(req.session_id, keep=50)

            yield _sse_event("metadata", {
                "domain": rag_response.domain,
                "confidence": rag_response.confidence,
                "confidence_level": _confidence_level(rag_response.confidence),
                "citations": rag_response.citations,
                "abstained": rag_response.abstained,
                "language": ctx.lang,
                "mode": rag_response.mode,
            })
            yield _sse_event("done", {})

        except Exception:
            logger.exception("Streaming chat route failed")
            answer = get_abstain_text(req.language)
            yield _sse_event("metadata", {
                "domain": "unknown", "confidence": 0.0,
                "confidence_level": "none", "citations": [],
                "abstained": True, "language": req.language,
            })
            for token in answer.split(" "):
                yield _sse_event("token", {"text": token + " "})
            yield _sse_event("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")
