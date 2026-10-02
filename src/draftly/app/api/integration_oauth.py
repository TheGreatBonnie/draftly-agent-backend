"""One-time organization-bound provider authorization state."""

from __future__ import annotations

import secrets

from fastapi import HTTPException

ALLOWED_INTEGRATION_RETURN_TO = frozenset(
    {
        "/onboarding/integrations",
        "/integrations/slack",
        "/integrations/discord",
    }
)


def validate_integration_return_to(return_to: str | None, *, default: str) -> str:
    """Resolve an OAuth return path without allowing an external redirect."""
    candidate = return_to or default
    if candidate not in ALLOWED_INTEGRATION_RETURN_TO:
        raise HTTPException(status_code=400, detail="Invalid return_to path")
    return candidate


async def create_state(db, provider: str, org_id: str, user_id: str, return_to: str) -> str:
    nonce = secrets.token_urlsafe(32)
    await db.execute(
        """INSERT INTO integration_oauth_states
           (nonce, provider, org_id, user_id, return_to, expires_at)
           VALUES ($1, $2, $3, $4, $5, now() + interval '10 minutes')""",
        nonce,
        provider,
        org_id,
        user_id,
        return_to,
    )
    return nonce


async def consume_state(db, provider: str, nonce: str) -> dict:
    if not nonce:
        raise HTTPException(status_code=400, detail="Missing authorization state")
    row = await db.fetch_one(
        """DELETE FROM integration_oauth_states
           WHERE nonce = $1 AND provider = $2 AND expires_at > now()
           RETURNING org_id, user_id, return_to""",
        nonce,
        provider,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired authorization state")
    return dict(row)
