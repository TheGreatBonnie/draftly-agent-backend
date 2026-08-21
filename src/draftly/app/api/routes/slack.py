
import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from draftly.app.api.auth import get_verified_token
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


@router.get("/install-url")
async def slack_install_url(token: dict = Depends(get_verified_token)) -> dict[str, str]:
    if not settings.slack_client_id or not settings.slack_redirect_uri:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured")
    scopes = "chat:write,channels:history,channels:read,groups:read,im:history,mpim:history"
    install_url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={settings.slack_client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={settings.slack_redirect_uri}"
    )
    return {"install_url": install_url}


@router.post("/link")
async def link_slack(
    request: LinkSlackRequest,
    token: dict = Depends(get_verified_token),
) -> dict[str, str]:
    """Link a Slack installation to the current Clerk organization."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.persistence.repositories.slack import link_slack_installation

    await link_slack_installation(team_id=request.team_id, org_id=org_id)
    return {"status": "linked", "team_id": request.team_id}


@router.delete("/installations/{team_id}")
async def delete_slack_installation(
    team_id: str,
    token: dict = Depends(get_verified_token),
) -> dict[str, str]:
    from draftly.persistence.repositories.slack import remove_slack_installation

    await remove_slack_installation(team_id=team_id)
    return {"status": "disconnected"}


@router.get("/oauth/callback")
async def slack_oauth_callback(code: str, state: str = "") -> RedirectResponse:
    """Exchange authorization code for tokens and save installation."""
    from slack_sdk.oauth.installation_store.models.installation import Installation

    from draftly.integrations.database.client import DatabaseClient
    from draftly.integrations.slack.installation_store import SlackInstallationStore

    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "code": code,
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok"):
        logger.error("slack_oauth_failed", error=data.get("error"))
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {data.get('error')}")

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

    db = DatabaseClient(database_url=settings.database_url)
    await db.start()
    store = SlackInstallationStore(db)
    await store.async_save(installation)

    logger.info("slack_oauth_success", team_id=team["id"], team_name=team["name"])

    frontend_url = f"{settings.frontend_url}/integrations/slack?team_id={team['id']}"
    return RedirectResponse(url=frontend_url)


@router.get("/installations")
async def slack_installations(token: dict = Depends(get_verified_token)) -> list[dict]:
    from draftly.persistence.repositories.slack import list_slack_installations

    return await list_slack_installations()


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
