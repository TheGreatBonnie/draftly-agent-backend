"""Discord interactions route with Ed25519 signature verification."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.app.config import get_settings
from draftly.integrations.discord.interactions import resolve_interaction_token

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/discord",
    tags=["discord"],
)

settings = get_settings()

ACTION_MAP = {
    "discord_approve": "approved",
    "discord_reject": "rejected",
    "discord_revise": "needs_changes",
    "discord_feedback": "needs_changes",
}

STATUS_COLOR = {
    "approved": 3066993,
    "rejected": 15158332,
    "needs_changes": 16776960,
}

STATUS_LABEL = {
    "approved": "Approved",
    "rejected": "Rejected",
    "needs_changes": "Changes Requested",
}


def _verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Ed25519 signature from Discord."""
    from draftly.security.webhook_verification import (
        WebhookVerificationError,
        WebhookVerifier,
    )

    try:
        return WebhookVerifier(discord_public_key=settings.discord_public_key).verify_discord(
            body, timestamp, signature
        )
    except (WebhookVerificationError, ValueError):
        return False


def _build_result_response(status: str, title: str) -> dict:
    """Build a Discord UPDATE_MESSAGE response with result embed."""
    return {
        "type": 7,
        "data": {
            "content": "",
            "embeds": [
                {
                    "title": f"Documentation Review — {STATUS_LABEL.get(status, status)}",
                    "description": (
                        f"**{title}**\n\n"
                        f"This review has been "
                        f"{STATUS_LABEL.get(status, status).lower()}."
                    ),
                    "color": STATUS_COLOR.get(status, 10070709),
                }
            ],
            "components": [],
        },
    }


@router.post("/interactions")
async def handle_interactions(request: Request) -> JSONResponse:
    """Handle Discord component interactions (button clicks, select menus)."""
    body = await request.body()

    timestamp = request.headers.get("X-Signature-Timestamp", "")
    signature = request.headers.get("X-Signature-Ed25519", "")
    if not timestamp or not signature:
        return JSONResponse(status_code=401, content={"error": "Missing signature"})

    if not _verify_signature(body, timestamp, signature):
        return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    payload = json.loads(body)

    interaction_type = payload.get("type")

    if interaction_type == 1:
        # PING
        return JSONResponse(content={"type": 1})

    if interaction_type == 3:
        # Component interaction
        custom_id = payload.get("data", {}).get("custom_id", "")
        parts = custom_id.split(":", 1)
        if len(parts) != 2:
            return JSONResponse(status_code=400, content={"error": "Invalid custom_id"})

        action_prefix, short_key = parts
        action = ACTION_MAP.get(action_prefix)
        if not action:
            return JSONResponse(status_code=400, content={"error": "Unknown action"})

        full_token = resolve_interaction_token(short_key)
        if not full_token:
            return JSONResponse(
                content={
                    "type": 4,
                    "data": {
                        "content": (
                            "This review link has expired or is invalid. "
                            "Please use the dashboard instead."
                        ),
                        "flags": 64,
                    },
                },
            )

        review_id = full_token  # full_token is the review UUID
        reviewer_id = payload.get("member", {}).get("user", {}).get("id", "unknown")

        decision_map = {
            "discord_approve": "approved",
            "discord_reject": "rejected",
            "discord_revise": "changes_requested",
            "discord_feedback": "changes_requested",
        }
        decision = decision_map.get(action_prefix, "rejected")
        feedback = payload.get("data", {}).get("components", [{}])[0].get("value", "")

        logger.info(
            "discord_review_decision_received",
            review_id=review_id,
            action=action_prefix,
            reviewer_id=reviewer_id,
        )

        try:
            # Get the ReviewDecisionService from app state
            review_decision = getattr(request.app.state.draftly, "review_decision", None)
            if review_decision is None:
                logger.error("review_decision_service_not_available")
                return JSONResponse(
                    content={
                        "type": 4,
                        "data": {
                            "content": "Service not available. Please use the dashboard.",
                            "flags": 64,
                        },
                    },
                )

            decision = action.split("_")[0] if "_" in action else action
            await review_decision.decide(
                review_id=review_id,
                decision=decision,
                reviewer_id=reviewer_id,
                comment=feedback,
            )
        except Exception as e:
            logger.error(
                "discord_review_complete_failed review_id=%s error=%s",
                review_id,
                str(e),
            )
            return JSONResponse(
                content={
                    "type": 4,
                    "data": {
                        "content": "Failed to process review. Please try the dashboard.",
                        "flags": 64,
                    },
                },
            )

        title = (
            payload.get("message", {})
            .get("embeds", [{}])[0]
            .get("description", "")
            .split("\n")[0]
            .replace("**Title:** ", "")
            .strip()
            or "Documentation"
        )

        return JSONResponse(content=_build_result_response(action, title))

    return JSONResponse(status_code=400, content={"error": "Unknown interaction type"})


# --- Settings endpoints ---


class LinkDiscordRequest(BaseModel):
    guild_id: str


class TriggerChannelsRequest(BaseModel):
    channels: list[str]


@router.get("/invite-url")
async def discord_invite_url() -> dict:
    """Return the Discord bot invite URL with required permissions."""
    app_id = settings.discord_app_id
    if not app_id:
        raise HTTPException(status_code=500, detail="Discord app ID not configured")
    # Permissions: View Channels + Send Messages + Send Messages in Threads + Add Reactions
    permissions = 4 + 2048 + 32768 + 64 + 2048  # 36932
    invite_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={app_id}"
        f"&permissions={permissions}"
        f"&scope=bot"
    )
    return {"invite_url": invite_url}


@router.post("/link")
async def link_discord(
    request: LinkDiscordRequest,
    token: dict = Depends(get_verified_token),
) -> dict:
    """Link a Discord guild to the current Clerk organization."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    await db.execute(
        "UPDATE organizations SET discord_guild_id = $1 WHERE clerk_org_id = $2",
        request.guild_id,
        org_id,
    )

    return {"status": "linked", "guild_id": request.guild_id}


@router.get("/status")
async def discord_status(token: dict = Depends(get_verified_token)) -> dict:
    """Return the Discord connection status for the current org."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    row = await db.fetch_one(
        "SELECT discord_guild_id FROM organizations WHERE clerk_org_id = $1",
        org_id,
    )
    connected = bool(row and row["discord_guild_id"])
    return {
        "connected": connected,
        "guild_id": row["discord_guild_id"] if row else None,
    }


@router.get("/channels")
async def discord_channels(token: dict = Depends(get_verified_token)) -> dict:
    """Fetch available text channels from the linked Discord guild."""
    import httpx as httpx_lib

    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    row = await db.fetch_one(
        "SELECT discord_guild_id FROM organizations WHERE clerk_org_id = $1",
        org_id,
    )
    if not row or not row["discord_guild_id"]:
        raise HTTPException(status_code=400, detail="Discord not linked")

    guild_id = row["discord_guild_id"]
    bot_token = settings.discord_bot_token

    if not bot_token:
        raise HTTPException(status_code=500, detail="Discord bot token not configured")

    async with httpx_lib.AsyncClient() as client:
        resp = await client.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch Discord channels")
        channels = resp.json()

    # Filter to text channels only (type 0 = text, type 5 = announcement, type 15 = forum)
    text_channel_types = {0, 5, 15}
    result = [
        {"id": ch["id"], "name": ch["name"], "type": ch["type"]}
        for ch in channels
        if ch.get("type") in text_channel_types
    ]
    return {"channels": result}


@router.get("/trigger-channels")
async def get_trigger_channels(token: dict = Depends(get_verified_token)) -> dict:
    """Return the configured trigger channels for the current org."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    row = await db.fetch_one(
        "SELECT discord_trigger_channels FROM organizations WHERE clerk_org_id = $1",
        org_id,
    )
    channels = row["discord_trigger_channels"] if row else []
    return {"channels": channels}


@router.post("/trigger-channels")
async def set_trigger_channels(
    request: TriggerChannelsRequest,
    token: dict = Depends(get_verified_token),
) -> dict:
    """Set the trigger channels for the current org."""
    import json

    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    await db.execute(
        "UPDATE organizations SET discord_trigger_channels = $1 WHERE clerk_org_id = $2",
        json.dumps(request.channels),
        org_id,
    )

    return {"channels": request.channels}


@router.delete("/link")
async def unlink_discord(token: dict = Depends(get_verified_token)) -> dict[str, str]:
    """Remove the Discord guild link from the current organization."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.app.dependencies import build_dependencies

    settings = get_settings()
    deps = build_dependencies(settings=settings)
    db = deps.integrations.database

    await db.execute(
        "UPDATE organizations "
        "SET discord_guild_id = NULL, discord_trigger_channels = NULL "
        "WHERE clerk_org_id = $1",
        org_id,
    )
    return {"status": "disconnected"}
