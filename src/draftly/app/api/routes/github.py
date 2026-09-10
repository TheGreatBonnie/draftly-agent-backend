import json
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token, require_reviewer_role
from draftly.app.composition.rq_jobs import enqueue_job
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


def _job_repos(request: Request) -> Any | None:
    """Resolve the jobs repository off the composed workflow context."""
    workflows = getattr(request.app.state.draftly, "workflows", None)
    context = getattr(workflows, "context", None) if workflows is not None else None
    repositories = getattr(context, "repositories", None) if context is not None else None
    return getattr(repositories, "jobs", None)


async def _resolve_webhook_identity(
    app_state: Any,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, int]:
    """Resolve a signed GitHub webhook to its Draftly organization.

    GitHub webhooks do not carry a Clerk organization claim. The GitHub App
    installation is the trust boundary, with the repository owner as a
    backwards-compatible fallback for older webhook payloads.
    """
    repository = str(event.get("repository") or "")
    owner = repository.split("/", 1)[0].strip()
    installation = payload.get("installation") or {}
    installation_id = int(installation.get("id") or 0)

    dependencies = getattr(app_state, "dependencies", None)
    integrations = getattr(dependencies, "integrations", None)
    db = getattr(integrations, "database", None)
    from draftly.persistence.repositories.github import get_org_by_github_org

    if not owner:
        raise HTTPException(status_code=422, detail="GitHub webhook has no repository")

    organization = await get_org_by_github_org(github_org=owner, db=db)
    if not organization:
        raise HTTPException(
            status_code=422,
            detail=f"GitHub organization '{owner}' is not linked to Draftly",
        )

    return str(organization["clerk_org_id"]), installation_id


def _webhook_task_name(event: dict[str, Any]) -> str:
    """Select the task whose workflow owns the normalized GitHub event."""
    prefix = str(event.get("event_type") or "").split(".", 1)[0]
    if prefix in {"issue_comment", "pull_request_review", "pull_request_review_comment"}:
        return "github_feedback.enqueue"
    if prefix == "release":
        return "github_release.enqueue"
    return "github_pr.enqueue"


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
    - pull_request — PR opened/closed/merged/updated
    - release — Release published/created/edited
    - push — Code pushed to branches

    Other GitHub webhook event types are rejected with 422 until a dedicated
    normalizer and workflow are registered for them.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if signature is None or not verify_webhook_signature(body, signature):
        logger.warning("github_webhook_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")


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

    # Only opened PRs proceed to the runner; drop other PR
    # actions at the edge. Defense-in-depth — the runner gate
    # (workflow/runner.py) is the authoritative filter for all entry paths.
    if str(event.get("event_type", "")).startswith("pull_request.") and not str(
        event.get("event_type", "")
    ).endswith(".opened"):
        logger.info(
            "github_webhook_pr_skipped",
            event_type=event.get("event_type"),
            delivery_id=delivery_id,
        )
        return WebhookResponse(status=f"{event.get('event_type')} (skipped, not opened)")

    run_id = str(event.get("event_id") or uuid4().hex)
    org_repo = str(event.get("repository") or "")
    org_id, installation_id = await _resolve_webhook_identity(
        app_state, event, payload
    )
    # The normalized event is the runner's persistence boundary. Carry the
    # resolved tenant identity forward so events, audit runs, and reviews use
    # the same organization as the jobs/workflow rows created below.
    event["project_id"] = org_id
    event["installation_id"] = installation_id
    jobs_repo = _job_repos(request)
    if jobs_repo is None:
        logger.error("github_webhook_jobs_unavailable", run_id=run_id)
        raise HTTPException(status_code=503, detail="Jobs store unavailable")
    try:
        await jobs_repo.upsert_on_conflict(
            run_id=run_id,
            org_id=org_id,
            name=(
                "github_release"
                if str(event.get("event_type", "")).startswith("release.")
                else "github_pr"
            ),
            job_type="github",
            schedule="webhook",
            configuration={
                "repository": org_repo,
                "event_type": str(event.get("event_type") or ""),
            },
        )
    except Exception as exc:
        logger.exception("github_webhook_jobs_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to register PR run") from exc

    # Register the workflow identity before dispatch so the run cannot exist
    # without its organization-scoped read-model row.
    try:
        pr = event.get("pull_request") or {}
        owner_repo = str(event.get("repository") or "")
        owner, _, repo = owner_repo.partition("/")
        from draftly.persistence.repositories.github import save_github_workflow
        deps = getattr(app_state, "dependencies", None)
        integrations = getattr(deps, "integrations", None) if deps else None
        db = getattr(integrations, "database", None)
        await save_github_workflow(
            org_id=org_id,
            workflow_id=run_id,
            run_id=run_id,
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            issue_number=int(pr.get("number") or 0),
            title=str(pr.get("title") or ""),
            actor=str(event.get("actor") or ""),
            event_type=str(event.get("event_type") or ""),
            db=db,
        )
    except Exception as exc:
        logger.exception("github_webhook_workflow_registration_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Failed to register GitHub workflow",
        ) from exc

    settings = getattr(app_state, "settings", None)
    rq_enabled = bool(getattr(settings, "rq_enabled", False)) if settings else False
    rq_queues = getattr(app_state, "rq_queues", None)
    task_handlers = getattr(app_state, "task_handlers", None)

    task_name = _webhook_task_name(event)
    if rq_enabled and rq_queues is not None and task_handlers is not None:
        job = enqueue_job(
            queues=rq_queues,
            task_handlers=task_handlers,
            task_name=task_name,
            event=event,
            run_id=run_id,
        )
        logger.info(
            "github_webhook_enqueued",
            task_name=task_name,
            run_id=run_id,
            rq_job_id=getattr(job, "id", ""),
        )
    else:
        # In-process fallback: run via the registered task so the path is
        # identical to RQ. Schedule on FastAPI BackgroundTasks so the webhook
        # returns to GitHub immediately (GitHub expects a fast 2xx) while the
        # worker executes the run after the response is sent.
        worker = getattr(app_state, "worker", None)
        if worker is None or getattr(worker, "run_task", None) is None:
            raise HTTPException(status_code=503, detail="Background worker is disabled")
        background_tasks.add_task(
            worker.run_task, task_name, event=event, run_id=run_id
        )
        logger.info(
            "github_webhook_dispatch_inprocess",
            task_name=task_name,
            run_id=run_id,
        )

    return WebhookResponse(status=f"{event.get('event_type')} (run_id={run_id})")


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
    token: dict = Depends(require_reviewer_role),
) -> dict[str, Any]:
    """Resume a graph after human review (plan §9.1).

    Loads the pending doc-review interrupt stored by the workflow runner,
    verifies the reviewer's organization, resumes the paused graph — and
    only records the decision once the workflow reached the expected terminal
    status. The shared resume service (plan §9.6) backs all platforms.
    """
    app_state = getattr(request.app.state, "draftly", None)
    if app_state is None:
        raise HTTPException(status_code=503, detail="Runtime not started")

    reviews_repo = getattr(getattr(app_state.dependencies, "repositories", None), "reviews", None)
    if reviews_repo is None:
        logger.warning("review_resume_store_unavailable", run_id=run_id)
        raise HTTPException(status_code=503, detail="Reviews store unavailable")

    from draftly.review.service import ReviewService

    outcomes_repo = getattr(
        getattr(app_state.dependencies, "repositories", None),
        "feedback_outcomes",
        None,
    )
    service = ReviewService(
        repository=reviews_repo,
        outcomes_repository=outcomes_repo,
    )
    pending = await service.get_by_run_id(run_id)
    if pending is None:
        logger.warning("review_resume_not_found", run_id=run_id)
        raise HTTPException(
            status_code=404,
            detail=f"No pending review for run {run_id}",
        )
    org_id = str(token.get("org_id") or "")
    if pending.org_id != org_id:
        logger.warning("review_resume_org_mismatch", run_id=run_id, org_id=org_id)
        raise HTTPException(status_code=404, detail=f"No pending review for run {run_id}")

    from draftly.review.resume import ReviewResumeError, resume_review_decision

    try:
        decision_kind = decision.normalized_decision()
        state = await resume_review_decision(
            review_id=pending.review_id,
            approved=decision.approved,
            decision=decision_kind,
            reviewer_id=str(token.get("user_id") or token.get("sub") or ""),
            comment=decision.comment or "",
            app_state=app_state,
            org_id=org_id,
        )
    except ReviewResumeError as exc:
        if "not pending" in str(exc) or "does not belong" in str(exc):
            logger.warning("review_resume_not_found", run_id=run_id)
            detail = f"No pending review for run {run_id}"
            raise HTTPException(status_code=404, detail=detail) from exc
        logger.warning("review_resume_conflict", run_id=run_id, error=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    status = state.status.value
    logger.info(
        "review_resumed",
        run_id=run_id,
        status=status,
        approved=bool(decision.approved),
        reviewer_id=str(token.get("user_id") or token.get("sub") or ""),
    )
    return {
        "status": "needs_changes" if decision_kind == "request_changes" else (
            "resumed" if decision_kind == "approve" else "rejected"
        ),
        "run_id": run_id,
        "workflow_status": status,
        "review": getattr(state, "decision_outcome", None),
        "rework_run_id": getattr(state, "run_id", None)
        if decision_kind == "request_changes"
        else None,
    }
