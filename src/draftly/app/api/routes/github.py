from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
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
from draftly.review.models import ReviewDecision

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


RETURN_TO_COOKIE = "gh_install_return_to"
ALLOWED_RETURN_TO = frozenset({"/onboarding/github", "/integrations/github"})


@router.get("/install-url")
async def github_install_url(
    response: Response,
    return_to: str | None = None,
    token: dict = Depends(get_verified_token),
) -> dict[str, str]:
    if not settings.github_app_slug:
        raise HTTPException(status_code=500, detail="GitHub App slug not configured")
    if return_to is not None:
        if return_to not in ALLOWED_RETURN_TO:
            raise HTTPException(status_code=400, detail="Invalid return_to path")
        response.set_cookie(
            RETURN_TO_COOKIE,
            return_to,
            max_age=600,
            httponly=True,
            samesite="lax",
            path="/api",
        )
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
async def github_installations(
    request: Request, token: dict = Depends(get_verified_token)
) -> list[dict]:
    repos = request.app.state.draftly.dependencies.repositories
    return await repos.github_installations.list_by_org(token.get("org_id") or "")


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
        repositories = [{"full_name": repo["full_name"], "id": repo["id"]} for repo in repos]
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
    request: Request,
    installation_id: int | None = None,
    setup_action: str | None = None,
) -> RedirectResponse:
    """GitHub App post-install redirect (official setup URL contract).

    GitHub redirects here after installation with ?installation_id= (and
    setup_action=install). Per GitHub docs this parameter is spoofable, so
    nothing is persisted from it here — the authenticated link happens later
    via POST /onboarding/github/connect, which validates the installation
    through the App-JWT-authed GitHub API before storing it. This endpoint
    only routes the browser back to where the install was initiated.
    """
    return_to = request.cookies.get(RETURN_TO_COOKIE)
    if return_to not in ALLOWED_RETURN_TO:
        return_to = "/integrations/github"
    frontend_url = f"{settings.frontend_url}{return_to}"
    if installation_id:
        frontend_url += f"?installation_id={installation_id}"
    response = RedirectResponse(url=frontend_url)
    response.delete_cookie(RETURN_TO_COOKIE, path="/api")
    return response


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

    # Normalize the raw webhook, then hand the event to the workflow
    # runner (§7.4): idempotency claim → per-run graph → outcome.
    payload["delivery_id"] = delivery_id
    app_state = getattr(request.app.state, "draftly", None)
    if app_state is None or app_state.events is None:
        raise HTTPException(status_code=503, detail="Runtime not started")

    try:
        event = await app_state.events.normalize_github(payload)
    except ValueError as exc:
        logger.warning("github_webhook_unhandled", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(app_state.workflows.runner.run, event)

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


@router.post("/review/{run_id}")
async def resume_review(
    run_id: str,
    decision: ReviewDecision,
    request: Request,
    _token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Resume a graph after human review (plan §9.1).

    Loads the pending doc-review interrupt stored by the workflow runner,
    records the decision, and — on approval — resumes the paused graph
    with the strands interrupt-response payload.
    """
    app_state = getattr(request.app.state, "draftly", None)
    if app_state is None:
        raise HTTPException(status_code=503, detail="Runtime not started")

    reviews_repo = getattr(getattr(app_state.dependencies, "repositories", None), "reviews", None)
    if reviews_repo is None:
        raise HTTPException(status_code=503, detail="Reviews store unavailable")

    from draftly.review.service import ReviewService

    service = ReviewService(repository=reviews_repo)
    pending = await service.get_by_run_id(run_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"No pending review for run {run_id}",
        )
    # Trust the stored review identity over the client-supplied one.
    decision.review_id = pending.review_id

    outcome = await service.decide(decision)

    from draftly.observability.metrics import metrics as _metrics

    _metrics.increment("draftly_review_decisions_total")

    if not decision.approved:
        logger.info("review_rejected run_id=%s", run_id)
        return {"status": "rejected", "run_id": run_id}

    interrupt_id = pending.interrupt_id
    if not interrupt_id:
        raise HTTPException(
            status_code=409,
            detail="Review has no interrupt to resume",
        )

    context = getattr(app_state.workflows, "context", None)
    surface = pending.workflow
    if surface not in ("pull_request", "issue", "support"):
        raise HTTPException(
            status_code=409,
            detail=f"Workflow {surface!r} is not resumable",
        )

    from draftly.integrations.strands.graph import build_graph_for_run
    from draftly.models.router import ModelRouter

    model = getattr(context, "model", None)
    if isinstance(model, ModelRouter):
        from draftly.integrations.strands.models import RoleAwareModelResolver

        model = RoleAwareModelResolver(model)

    graph = build_graph_for_run(
        run_id,
        surface=surface,
        tools_registry=getattr(context, "tools", None),
        model=model,
        hooks=list(getattr(context, "hooks", []) or []),
        storage_dir=getattr(context, "storage_dir", ".draftly/sessions"),
        memory=getattr(context, "memory", None),
    )
    resume_input = service.approvals.build_resume_input(
        interrupt_id,
        {"approved": True, "comment": decision.comment},
    )
    try:
        result = await graph.invoke_async(
            resume_input,
            invocation_state={"run_id": run_id},
        )
    except RuntimeError as exc:
        logger.warning("review_resume_failed run_id=%s: %s", run_id, exc)
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} could not be resumed: {exc}",
        ) from exc

    status = str(getattr(result, "status", result))
    logger.info("review_resumed run_id=%s status=%s", run_id, status)
    return {
        "status": status,
        "run_id": run_id,
        "review": outcome.get("review") if isinstance(outcome, dict) else None,
    }
