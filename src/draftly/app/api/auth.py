from __future__ import annotations

import base64

import jwt  # ty: ignore[unresolved-import]
import structlog  # ty: ignore[unresolved-import]
from fastapi import Depends, HTTPException, Request  # ty: ignore[unresolved-import]

from draftly.app.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        encoded = settings.clerk_publishable_key.split("_")[-1]  # ty: ignore[unresolved-attribute]
        # URL-safe base64 → standard base64 + padding
        padded = encoded.replace("-", "+").replace("_", "/")
        padded += "=" * (4 - len(padded) % 4) if len(padded) % 4 else ""
        domain = base64.b64decode(padded).decode().split("$")[0]
        jwks_url = f"https://{domain}/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    return _jwks_client


class ClerkAuthError(HTTPException):
    def __init__(self, detail: str = "Invalid authentication"):
        super().__init__(status_code=401, detail=detail)


async def get_verified_token(request: Request) -> dict:
    """FastAPI dependency: extracts and verifies the Clerk JWT from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ClerkAuthError("Missing or malformed Authorization header")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise ClerkAuthError("Empty token")

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
    except jwt.PyJWTError as e:
        logger.warning("jwt_verification_failed", error=str(e))
        raise ClerkAuthError("Invalid or expired token")

    user_id = payload.get("sub")

    if not user_id:
        raise ClerkAuthError("Token missing user identifier")

    # JWT v2: org claim is nested at payload["o"] with role at "rol"
    # JWT v1 (deprecated): flat payload["org_role"] with "org:" prefix
    org_claim = payload.get("o", {})
    if isinstance(org_claim, dict) and org_claim:
        org_id = org_claim.get("id", "")
        org_role = org_claim.get("rol", "")
    else:
        org_id = payload.get("org_id", "")
        org_role = payload.get("org_role", "")

    # Normalize: strip "org:" prefix if present (v1 format)
    if org_role.startswith("org:"):
        org_role = org_role[4:]

    return {
        "user_id": user_id,
        "org_id": org_id,
        "org_role": org_role,
        "raw": payload,
    }


async def require_admin_role(token: dict = Depends(get_verified_token)) -> dict:
    """Require the user to have admin role in the current organization."""
    if token.get("org_role") not in ("admin",):
        raise HTTPException(
            status_code=403,
            detail="Admin role required for this action",
        )
    return token


async def require_reviewer_role(token: dict = Depends(get_verified_token)) -> dict:
    """Require the user to have reviewer or admin role."""
    if token.get("org_role") not in ("reviewer", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Reviewer role required for this action",
        )
    return token
