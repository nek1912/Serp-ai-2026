from datetime import UTC, datetime, timedelta

from app.db import get_supabase


def get_state(session_id: str) -> str | None:
    """Session is authoritative for jurisdiction (spec §2.3, P1-7): a request
    with state=null continues in the session's previously selected state."""
    try:
        rows = (get_supabase().table("sessions").select("state")
                .eq("session_id", session_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return (rows[0].get("state") or {}).get("selected_state")


def touch_session(session_id: str, selected_state: str | None, language: str) -> None:
    sb = get_supabase()
    try:
        sb.rpc("purge_expired_sessions", {}).execute()
    except Exception:
        pass
    expires = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    try:
        sb.table("sessions").upsert({
            "session_id": session_id,
            "state": {"selected_state": selected_state, "language": language},
            "expires_at": expires,
        }, on_conflict="session_id").execute()
    except Exception:
        pass  # Non-UUID session IDs or missing table — silently skip


def save_message(session_id: str, role: str, content: str) -> None:
    """Insert a message into the messages table."""
    try:
        get_supabase().table("messages").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        pass  # Non-UUID session IDs or missing table — silently skip


def get_history(session_id: str, limit: int = 8) -> list[dict]:
    """Retrieve the last N messages for a session, oldest first."""
    try:
        resp = (
            get_supabase().table("messages")
            .select("role", "content")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        rows.reverse()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception:
        return []


def trim_messages(session_id: str, keep: int = 50) -> None:
    """Delete messages beyond the most recent `keep` for a session."""
    try:
        resp = (
            get_supabase().table("messages")
            .select("id")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .range(keep, 10000)
            .execute()
        )
        ids = [r["id"] for r in (resp.data or [])]
        if ids:
            get_supabase().table("messages").delete().in_("id", ids).execute()
    except Exception:
        pass
