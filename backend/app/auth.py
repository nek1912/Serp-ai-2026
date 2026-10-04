"""Clerk JWT verification for FastAPI."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import Request, HTTPException
from jose import jwt, jwk, JWTError

from app.config import get_settings

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600  # re-fetch keys every hour

_jwks_cache: dict = {}
_jwks_url: str = ""
_jwks_fetched_at: float = 0.0


async def _get_jwks() -> dict:
    """Fetch and cache Clerk's JWKS keys with TTL."""
    global _jwks_cache, _jwks_url, _jwks_fetched_at

    s = get_settings()
    if not s.clerk_issuer:
        return {}

    url = f"{s.clerk_issuer}/.well-known/jwks.json"
    now = time.monotonic()

    if _jwks_cache and _jwks_url == url and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_url = url
            _jwks_fetched_at = now
            return _jwks_cache
    except Exception:
        logger.exception("Failed to fetch Clerk JWKS")
        return _jwks_cache  # return stale cache on failure


async def verify_clerk_token(request: Request) -> str | None:
    """Extract and verify Clerk session JWT. Returns user_id or None."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    s = get_settings()
    if not s.clerk_issuer or not s.clerk_secret_key:
        return None
    try:
        jwks = await _get_jwks()
        if not jwks:
            return None

        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key_data:
            return None

        public_key = jwk.construct(key_data)
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=s.clerk_issuer,
        )
        return payload.get("sub")
    except JWTError:
        return None


async def require_auth(request: Request) -> str:
    """FastAPI dependency — returns user_id or raises 401."""
    user_id = await verify_clerk_token(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id
