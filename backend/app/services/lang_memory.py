"""In-memory per-session response-language store.

Single-instance / demo use ONLY. This is NOT persistent and does not cover
multi-worker production deployments. The resolver (resolve_response_language)
is the sole owner of reading/writing session language; this module only
provides the storage primitive.
"""

from threading import Lock

_session_language: dict[str, str] = {}
_lock = Lock()

DEFAULT_LANGUAGE = "en"


def get_session_language(session_id: str) -> str | None:
    with _lock:
        return _session_language.get(session_id)


def set_session_language(session_id: str, language: str) -> None:
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if not language or not isinstance(language, str):
        raise ValueError("language must be a non-empty string")
    with _lock:
        _session_language[session_id] = language


def clear_session_language(session_id: str) -> None:
    with _lock:
        _session_language.pop(session_id, None)
