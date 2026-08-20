from typing import Any

import structlog
from events.github_events import GitHubEventProcessor
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.app.config import get_settings
from draftly.integrations.github.app_auth import (
    get_installation_info,
    get_installation_repositories,
    get_installation_token,
    verify_webhook_signature,
)

logger = structlog.get_logger()

router = APIRouter(
    prefix="/github",
    tags=["github"],
)

settings = get_settings()


class WebhookResponse(BaseModel):
    status: str


class LinkGitHubRequest(BaseModel):
    installation_id: int


@router.get("/install-url")
async def github_install_url(token: dict = Depends(get_verified_token)) -> dict[str, str]:
    if not settings.github_app_slug:
        raise HTTPException(status_code=500, detail="GitHub App slug not configured")
    return {"install_url": f"https://github.com/apps/{settings.github_app_slug}/installations/new"}


@router.delete("/installations/{installation_id}")
async def delete_github_installation(
    installation_id: int,
    token: dict = Depends(get_verified_token),
) -> dict[str, str]:
    from draftly.persistence.repositories.github import remove_github_installation

    await remove_github_installation(installation_id=installation_id)
    return {"status": "disconnected"}


@router.get("/installations")
async def github_installations(token: dict = Depends(get_verified_token)) -> list[dict]:
    from draftly.persistence.repositories.github import list_github_installations

    return await list_github_installations()


@router.post("/link")
async def link_github(
    request: LinkGitHubRequest,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Link a GitHub App installation to the current Clerk organization."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    logger.info(
        "github_link_requested",
        installation_id=request.installation_id,
        org_id=org_id,
    )

    # Look up installation details from GitHub API
    try:
        info = await get_installation_info(request.installation_id)
    except Exception as e:
        logger.error(
            "github_link_failed_lookup",
            installation_id=request.installation_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to look up GitHub installation") from e

    account = info.get("account") or {}
    github_org = account.get("login")
    if not github_org:
        logger.warning("github_link_no_account", installation_id=request.installation_id)
        raise HTTPException(status_code=400, detail="Invalid installation: no account found")

    # Check if this GitHub org is already linked to a different Clerk org
    from draftly.persistence.repositories.github import get_org_by_github_org

    existing = await get_org_by_github_org(github_org=github_org)
    if existing and existing["clerk_org_id"] != org_id:
        logger.warning(
            "github_link_org_conflict",
            github_org=github_org,
            existing_org_id=existing["clerk_org_id"],
            requested_org_id=org_id,
        )
        raise HTTPException(
            status_code=409,
            detail=f"GitHub org '{github_org}' is already linked to another organization",
        )

    # Link: update org's github_org column
    from draftly.persistence.repositories.organizations import update_org_github

    await update_org_github(org_id=org_id, github_org=github_org)

    # Store the installation record
    try:
        install_token = await get_installation_token(request.installation_id)
        repos = await get_installation_repositories(install_token)
        repositories = [
            {"full_name": repo["full_name"], "id": repo["id"]}
            for repo in repos
        ]
    except Exception as e:
        logger.warning(
            "github_link_failed_fetch_repos",
            installation_id=request.installation_id,
            error=str(e),
        )
        repositories = []

    from draftly.persistence.repositories.github import store_github_installation

    await store_github_installation(
        org_id=org_id,
        installation_id=request.installation_id,
        github_org=github_org,
        repositories=repositories,
    )

    logger.info(
        "github_link_success",
        installation_id=request.installation_id,
        github_org=github_org,
        org_id=org_id,
        repo_count=len(repositories),
    )

    return {"status": "linked", "github_org": github_org}


@router.get("/setup-callback")
async def github_setup_callback(
    installation_id: int | None = None,
    setup_action: str | None = None,
) -> RedirectResponse:
    settings = get_settings()
    frontend_url = f"{settings.frontend_url}/integrations/github"
    if installation_id:
        frontend_url += f"?installation_id={installation_id}"
    return RedirectResponse(url=frontend_url)


@router.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """
    Handle incoming GitHub webhooks.

    Supported events:
    - installation (created/deleted) — GitHub App lifecycle
    - issues — Issue opened/closed/reopened/updated
    - issue_comment — Comment on issues
    - pull_request — PR opened/closed/merged/updated
    - pull_request_review — PR review activity
    - release — Release published/created/edited
    - push — Code pushed to branches
    - repository — Repository created/updated
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if signature is None or not verify_webhook_signature(body, signature):
        logger.warning("github_webhook_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    import json

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("github_webhook_invalid_json")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = request.headers.get("X-GitHub-Event")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")
    if not event_type:
        logger.warning("github_webhook_missing_event_type", delivery_id=delivery_id)
        raise HTTPException(status_code=400, detail="Missing GitHub event type")

    logger.info(
        "github_webhook_received",
        event_type=event_type,
        delivery_id=delivery_id,
    )

    # Handle installation events (created/deleted) — special case
    if event_type == "installation":
        return await _handle_installation_event(payload)

    # All other events → delegate to GitHubEventProcessor
    # The processor translates raw GitHub events into Draftly events
    # and publishes them to the EventBus for workflow handlers
    processor = GitHubEventProcessor()
    background_tasks.add_task(
        processor.process,
        event_type,
        payload,
    )

    logger.info(
        "github_webhook_queued",
        event_type=event_type,
        delivery_id=delivery_id,
    )

    return {"accepted": True, "status": f"Processing {event_type} event"}


async def _handle_installation_event(payload: dict) -> WebhookResponse:
    """
    Handle GitHub App installation created/deleted events.

    This is handled separately from other events because it manages
    the Draftly ↔ GitHub App relationship, not repository content.
    """
    action = payload.get("action")
    installation = payload.get("installation")

    if not installation:
        logger.warning("github_installation_missing_payload")
        raise HTTPException(status_code=400, detail="Missing installation in payload")

    installation_id = installation["id"]
    account = installation.get("account") or {}
    github_org = account.get("login", "unknown")

    logger.info(
        "github_installation_event",
        action=action,
        installation_id=installation_id,
        github_org=github_org,
    )

    if action == "created":
        repositories = [
            {"full_name": repo["full_name"], "id": repo["id"]}
            for repo in payload.get("repositories", [])
        ]
        from draftly.persistence.repositories.github import (
            get_org_by_github_org,
            store_github_installation,
        )

        org = await get_org_by_github_org(github_org=github_org)
        if not org:
            logger.warning(
                "github_installation_org_not_found",
                github_org=github_org,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Organization '{github_org}' not found. Create it via Clerk first.",
            )
        org_id = org["clerk_org_id"]
        await store_github_installation(
            org_id=org_id,
            installation_id=installation_id,
            github_org=github_org,
            repositories=repositories,
        )
        logger.info(
            "github_installation_created",
            installation_id=installation_id,
            github_org=github_org,
            org_id=org_id,
            repo_count=len(repositories),
        )
        return WebhookResponse(status="Installation created")

    elif action == "deleted":
        from draftly.persistence.repositories.github import remove_github_installation

        await remove_github_installation(installation_id=installation_id)
        logger.info(
            "github_installation_deleted",
            installation_id=installation_id,
            github_org=github_org,
        )
        return WebhookResponse(status="Installation deleted")

    logger.info("github_installation_unhandled_action", action=action)
    return WebhookResponse(status=f"Installation {action} (unhandled)")
