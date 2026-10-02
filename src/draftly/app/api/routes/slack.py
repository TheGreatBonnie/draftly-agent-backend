from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from draftly.app.api.auth import get_verified_token, require_admin_role
from draftly.app.api.integration_oauth import (
    consume_state,
    create_state,
    validate_integration_return_to,
)
from draftly.app.config import get_settings
from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.slack.app import SlackAppDeps, build_slack_app, register_handlers
from draftly.integrations.slack.installation_store import SlackInstallationStore

logger = structlog.get_logger()

router = APIRouter(
    prefix="/slack",
    tags=["slack"],
)

_slack_app = None
_handler = None


def _get_handler() -> AsyncSlackRequestHandler:
    global _slack_app, _handler
    if _handler is None:
        settings = get_settings()
        db = DatabaseClient(database_url=settings.database_url)
        installation_store = SlackInstallationStore(db)
        _slack_app = build_slack_app(
            signing_secret=settings.slack_signing_secret,
            installation_store=installation_store,
        )
        deps = SlackAppDeps(db=db)
        register_handlers(_slack_app, deps)
        _handler = AsyncSlackRequestHandler(_slack_app)
    return _handler


class LinkSlackRequest(BaseModel):
    team_id: str


settings = get_settings()

SLACK_BOT_SCOPES = (
    "app_mentions:read",
    "channels:history",
    "channels:read",
    "chat:write",
    "groups:history",
    "groups:read",
    "im:history",
    "mpim:history",
    "reactions:write",
)


@router.get("/install-url")
async def slack_install_url(
    request: Request,
    return_to: str | None = None,
    token: dict = Depends(require_admin_role),
) -> dict[str, str]:
    if not settings.slack_client_id or not settings.slack_redirect_uri:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured")
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    db = request.app.state.draftly.dependencies.integrations.database
    safe_return_to = validate_integration_return_to(
        return_to,
        default="/integrations/slack",
    )
    nonce = await create_state(db, "slack", org_id, token["user_id"], safe_return_to)
    scopes = ",".join(sorted(set(settings.slack_scopes or SLACK_BOT_SCOPES)))
    params = {
        "client_id": settings.slack_client_id,
        "scope": scopes,
        "redirect_uri": settings.slack_redirect_uri,
        "state": nonce,
    }
    install_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return {"install_url": install_url}


@router.post("/link")
async def link_slack(
    request: LinkSlackRequest,
    token: dict = Depends(require_admin_role),
) -> dict[str, str]:
    """Link a Slack installation to the current Clerk organization."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    db = DatabaseClient(database_url=settings.database_url)
    existing = await db.fetch_one(
        "SELECT org_id FROM slack_installations WHERE team_id = $1", request.team_id
    )
    if not existing or existing["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "linked", "team_id": request.team_id}


@router.delete("/installations/{team_id}")
async def delete_slack_installation(
    team_id: str,
    token: dict = Depends(require_admin_role),
) -> dict[str, str]:
    db = DatabaseClient(database_url=settings.database_url)
    result = await db.fetch_one(
        "DELETE FROM slack_installations WHERE team_id = $1 AND org_id = $2 RETURNING team_id",
        team_id,
        token.get("org_id") or "",
    )
    if not result:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disconnected"}


def _oauth_result_url(return_to: str, status: str) -> str:
    query = urlencode({"oauth_provider": "slack", "oauth_status": status})
    return f"{settings.frontend_url}{return_to}?{query}"


@router.get("/oauth/callback")
async def slack_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str = "",
    error: str | None = None,
) -> RedirectResponse:
    """Exchange authorization code for tokens and save installation."""
    from slack_sdk.oauth.installation_store.models.installation import Installation

    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured")

    db = request.app.state.draftly.dependencies.integrations.database
    oauth_state = await consume_state(db, "slack", state)
    return_to = validate_integration_return_to(
        oauth_state.get("return_to"),
        default="/integrations/slack",
    )
    if error:
        status = "cancelled" if error == "access_denied" else "failed"
        return RedirectResponse(url=_oauth_result_url(return_to, status))
    if not code:
        return RedirectResponse(url=_oauth_result_url(return_to, "failed"))

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "code": code,
                    "client_id": settings.slack_client_id,
                    "client_secret": settings.slack_client_secret,
                    "redirect_uri": settings.slack_redirect_uri,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if not data.get("ok"):
            raise RuntimeError("Slack rejected the OAuth exchange")

        team = data["team"]
        authed_user = data.get("authed_user", {})
        installation = Installation(
            team_id=team["id"],
            team_name=team["name"],
            bot_user_id=data.get("bot_user_id", ""),
            bot_token=data["access_token"],
            bot_scopes=data.get("scope", "").split(","),
            user_id=authed_user.get("id"),
            user_token=authed_user.get("access_token"),
            user_scopes=authed_user.get("scope", "").split(","),
            token_type="bot",
        )

        store = SlackInstallationStore(db)
        owner = await db.fetch_one(
            "SELECT org_id FROM slack_installations WHERE team_id = $1", team["id"]
        )
        if owner and owner["org_id"] and owner["org_id"] != oauth_state["org_id"]:
            raise RuntimeError("Slack workspace already belongs to another organization")
        await store.async_save(installation, org_id=oauth_state["org_id"])
        bus = getattr(request.app.state, "dashboard_broadcaster", None)
        if bus is not None:
            await bus.broadcast(
                oauth_state["org_id"],
                "integration:changed",
                {"provider": "slack"},
            )
        logger.info("slack_oauth_success", team_id=team["id"], team_name=team["name"])
    except Exception:
        logger.exception("slack_oauth_callback_failed", org_id=oauth_state["org_id"])
        return RedirectResponse(url=_oauth_result_url(return_to, "failed"))

    return RedirectResponse(url=_oauth_result_url(return_to, "connected"))


@router.get("/installations")
async def slack_installations(
    request: Request, token: dict = Depends(get_verified_token)
) -> list[dict]:
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    db = request.app.state.draftly.dependencies.integrations.database
    rows = await db.fetch_all(
        """SELECT id::text, team_id, team_name, bot_user_id, installed_at, updated_at
           FROM slack_installations WHERE org_id = $1 ORDER BY installed_at DESC""",
        org_id,
    )
    return [dict(row) for row in rows]


@router.post("/events")
async def slack_events(
    request: Request,
) -> Response:
    """Handle Slack Events API webhooks via Bolt adapter."""
    handler = _get_handler()
    return await handler.handle(request)


@router.post("/interactivity")
async def slack_interactivity(
    request: Request,
) -> Response:
    """Handle Slack interactivity webhooks (button clicks, dropdowns)."""
    handler = _get_handler()
    return await handler.handle(request)
