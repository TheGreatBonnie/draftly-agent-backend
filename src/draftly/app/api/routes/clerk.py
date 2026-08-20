"""Clerk webhook handler for organization lifecycle events."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import structlog  # ty: ignore[unresolved-import]
from fastapi import APIRouter, HTTPException, Request  # ty: ignore[unresolved-import]
from pydantic import BaseModel  # ty: ignore[unresolved-import]

from draftly.app.config import get_settings
from draftly.persistence.repositories.organizations import get_or_create_org_by_clerk

logger = structlog.get_logger()

router = APIRouter(
    prefix="/clerk",
    tags=["clerk"],
)


class WebhookResponse(BaseModel):
    status: str


def verify_svix_signature(payload: bytes, headers: dict[str, str]) -> bool:
    """Verify Svix webhook signature (used by Clerk)."""
    svix_id = headers.get("svix-id")
    svix_timestamp = headers.get("svix-timestamp")
    svix_signature = headers.get("svix-signature")

    if not svix_id or not svix_timestamp or not svix_signature:
        return False

    settings = get_settings()
    raw_secret = settings.clerk_signing_secret or ""
    if not raw_secret:
        return False

    signing_key = base64.b64decode(raw_secret.removeprefix("whsec_"))
    to_sign = f"{svix_id}.{svix_timestamp}.{payload.decode()}"
    expected = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).digest()
    expected_b64 = base64.b64encode(expected).decode()

    for sig in svix_signature.split(" "):
        if sig.startswith("v1,"):
            received = sig[3:]
            if hmac.compare_digest(received, expected_b64):
                return True
    return False


@router.post("/webhook")
async def clerk_webhook(request: Request) -> WebhookResponse:
    """Receive Clerk webhook events for organization lifecycle."""
    body = await request.body()
    headers = dict(request.headers)

    if not verify_svix_signature(body, headers):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event_type = payload.get("type", "")
    data = payload.get("data", {})

    logger.info("clerk_webhook_received", event_type=event_type)

    if event_type == "organization.created":
        await get_or_create_org_by_clerk(
            clerk_org_id=data["id"],
            name=data.get("name", "Unnamed Organization"),
        )

    elif event_type == "organization.deleted":
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb
        await db.execute("DELETE FROM organizations WHERE clerk_org_id = $1", data["id"])
        logger.info("org_deleted_from_clerk", clerk_org_id=data["id"])

    elif event_type == "organization.updated":
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb
        await db.execute(
            "UPDATE organizations SET clerk_org_name = $1 WHERE clerk_org_id = $2",
            data.get("name", ""),
            data["id"],
        )

    return WebhookResponse(status="ok")
