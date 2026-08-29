"""Onboarding REST API routes."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token
from draftly.app.composition.rq_jobs import enqueue_job
from draftly.app.services.init_lock import (
    force_release_init_lock,
    release_init_lock,
    try_acquire_init_lock,
)
from draftly.workflows.onboarding.initialize import STAGE_LABELS, STAGES

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/onboarding",
    tags=["onboarding"],
    dependencies=[Depends(get_verified_token)],
)

REQUIRED_STEPS = {"workspace", "github", "repository", "documentation", "initialization"}

# Linear §5.2 state machine: target state -> states it may be reached from.
# Same-state replays are allowed (idempotent POSTs); skipping is not.
_TRANSITIONS: dict[str, set[str]] = {
    "WORKSPACE_CREATED": {"NOT_STARTED", "WORKSPACE_CREATED"},
    "GITHUB_CONNECTED": {"WORKSPACE_CREATED", "GITHUB_CONNECTED"},
    "REPOSITORY_SELECTED": {"GITHUB_CONNECTED", "REPOSITORY_SELECTED"},
    "DOCUMENTATION_DISCOVERED": {"REPOSITORY_SELECTED", "DOCUMENTATION_DISCOVERED"},
    "INTEGRATIONS_CONFIGURED": {"DOCUMENTATION_DISCOVERED", "INTEGRATIONS_CONFIGURED"},
    "PREFERENCES_CONFIGURED": {"INTEGRATIONS_CONFIGURED", "PREFERENCES_CONFIGURED"},
}


def _repos(request: Request):
    """Repositories bundle via composition — mirrors routes/documentation.py."""
    return request.app.state.draftly.dependencies.repositories


def _worker(request: Request):
    """Application worker or None — mirrors routes/jobs.py."""
    worker = getattr(request.app.state.draftly, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Background worker is disabled")
    return worker


def _org_id(token: dict) -> str:
    """Extract and validate the organization ID from the verified token."""
    org_id = token.get("org_id")
    if not isinstance(org_id, str):
        raise HTTPException(status_code=401, detail="Missing organization ID")
    return org_id


def _selected(current: dict | None) -> dict:
    """Selected-repository metadata blob for the onboarding record."""
    return (current or {}).get("selected_repository") or {}


def _require_transition(current: dict | None, target: str, action: str) -> str:
    """Enforce the linear §5.2 machine; returns the current state."""
    current_state = (current or {}).get("state", "NOT_STARTED")
    if current_state not in _TRANSITIONS[target]:
        raise HTTPException(status_code=409, detail=f"Cannot {action} from {current_state}")
    return current_state


class WorkspaceRequest(BaseModel):
    name: str
    description: str | None = None


class GitHubConnectRequest(BaseModel):
    installation_id: int


class RepositoryRequest(BaseModel):
    full_name: str
    default_branch: str = "main"


class SourcesRequest(BaseModel):
    include: list[str] | None = None
    exclude: list[str] | None = None


class IntegrationsRequest(BaseModel):
    slack: bool = False
    discord: bool = False


class PreferencesRequest(BaseModel):
    style: str | None = None
    review_policy: str = "always"
    auto_publish: bool = False


@router.get("/status")
async def get_status(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    state = await repos.onboarding.get(org_id)
    if state is None:
        return {"state": "NOT_STARTED", "completed_steps": [], "failure": None}
    return state


@router.post("/workspace")
async def create_workspace(
    body: WorkspaceRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "WORKSPACE_CREATED", "create workspace")
    stage_config = [
        {"id": s, "label": STAGE_LABELS.get(s, s), "order": i}
        for i, s in enumerate(STAGES)
    ]
    await repos.onboarding.upsert(
        org_id,
        state="WORKSPACE_CREATED",
        selected_repository={"workspace_name": body.name, "description": body.description},
        stage_config=stage_config,
    )
    await repos.onboarding.mark_step(org_id, "workspace")
    return {"state": "WORKSPACE_CREATED"}


@router.post("/github/connect")
async def connect_github(
    body: GitHubConnectRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "GITHUB_CONNECTED", "connect GitHub")
    from draftly.integrations.github.app_auth import get_installation_info
    from draftly.persistence.repositories.github import store_github_installation
    from draftly.persistence.repositories.organizations import update_org_github

    info = await get_installation_info(body.installation_id)
    account = info.get("account")
    github_org = account.get("login") if isinstance(account, dict) else None
    if not isinstance(github_org, str) or not github_org:
        raise HTTPException(
            status_code=502,
            detail="GitHub installation has no organization account",
        )
    await store_github_installation(
        org_id=org_id,
        installation_id=body.installation_id,
        github_org=github_org,
    )
    await update_org_github(org_id=org_id, github_org=github_org)
    await repos.onboarding.upsert(
        org_id,
        state="GITHUB_CONNECTED",
        selected_repository={
            **_selected(current),
            "github_org": github_org,
            "installation_id": body.installation_id,
        },
    )
    await repos.onboarding.mark_step(org_id, "github")
    return {"state": "GITHUB_CONNECTED", "github_org": github_org}


@router.get("/github/repositories")
async def list_github_repositories(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """List repositories accessible via the linked installation."""
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    installation_id = _selected(current).get("installation_id")
    if not installation_id:
        raise HTTPException(status_code=409, detail="GitHub not connected yet")
    from draftly.integrations.github.app_auth import get_installation_token
    from draftly.integrations.github.auth import GitHubAuth
    from draftly.integrations.github.client import GitHubClient

    tok = await get_installation_token(int(installation_id))
    github = GitHubClient(auth=GitHubAuth(token=tok))
    repositories = await github.get_installation_repositories(tok)
    return {"repositories": repositories}


@router.post("/repository")
async def select_repository(
    body: RepositoryRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "REPOSITORY_SELECTED", "select repository")
    installation_id = _selected(current).get("installation_id")
    if not installation_id:
        raise HTTPException(status_code=409, detail="GitHub not connected yet")
    if "/" not in body.full_name:
        raise HTTPException(status_code=422, detail="full_name must be 'owner/repo'")
    from draftly.integrations.github.app_auth import get_installation_token
    from draftly.integrations.github.auth import GitHubAuth
    from draftly.integrations.github.client import GitHubClient

    tok = await get_installation_token(int(installation_id))
    github = GitHubClient(auth=GitHubAuth(token=tok))
    accessible = await github.get_installation_repositories(tok)
    if body.full_name not in {r.get("full_name") for r in accessible}:
        raise HTTPException(
            status_code=422,
            detail=f"Repository {body.full_name} is not accessible via this installation",
        )
    await repos.repository_config.upsert(
        org_id, body.full_name, default_branch=body.default_branch, installation_id=installation_id
    )
    await repos.onboarding.upsert(
        org_id,
        state="REPOSITORY_SELECTED",
        selected_repository={
            **_selected(current),
            "full_name": body.full_name,
            "default_branch": body.default_branch,
        },
    )
    await repos.onboarding.mark_step(org_id, "repository")
    return {"state": "REPOSITORY_SELECTED", "repository": body.full_name}


@router.post("/documentation/discover")
async def discover_documentation(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    repo_full = _selected(current).get("full_name", "")
    if not repo_full or "/" not in repo_full:
        raise HTTPException(status_code=409, detail="Select a valid repository before discovery")
    from draftly.documentation.discovery import discover_documentation as discover
    from draftly.integrations.github.app_auth import get_installation_token
    from draftly.integrations.github.client import GitHubClient

    installation_id = _selected(current).get("installation_id")
    if not installation_id:
        raise HTTPException(status_code=409, detail="No GitHub App installation configured")
    from draftly.integrations.github.auth import GitHubAuth

    tok = await get_installation_token(int(installation_id))
    github = GitHubClient(auth=GitHubAuth(token=tok))
    owner, repo = repo_full.split("/", 1)
    repo_info = await github.get_repository(repo_full)
    default_branch = repo_info.get("default_branch", "main")
    tree = await github.get_tree(owner, repo, default_branch, tok)
    paths = [e["path"] for e in tree if e.get("type") == "blob"]
    candidates = discover(
        paths,
        ["README.md", "docs/**", "*.md", "*.mdx"],
        ["node_modules/**", "dist/**"],
    )
    return {"candidates": candidates, "count": len(candidates), "total_files": len(paths)}


@router.post("/sources")
async def confirm_sources(
    body: SourcesRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "DOCUMENTATION_DISCOVERED", "confirm sources")
    repo_full = _selected(current).get("full_name", "")
    if body.include or body.exclude:
        await repos.repository_config.upsert(
            org_id, repo_full, doc_include=body.include, doc_exclude=body.exclude
        )
        # Mirror into selected_repository so the initialize workflow (which
        # receives that dict) picks up the user's confirmed paths.
        await repos.onboarding.upsert(
            org_id,
            selected_repository={
                **_selected(current),
                "doc_include": body.include,
                "doc_exclude": body.exclude,
            },
        )
    await repos.onboarding.upsert(org_id, state="DOCUMENTATION_DISCOVERED")
    await repos.onboarding.mark_step(org_id, "documentation")
    return {"state": "DOCUMENTATION_DISCOVERED"}


@router.post("/integrations")
async def configure_integrations(
    body: IntegrationsRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "INTEGRATIONS_CONFIGURED", "configure integrations")
    await repos.onboarding.upsert(
        org_id,
        state="INTEGRATIONS_CONFIGURED",
        selected_repository={
            **_selected(current),
            "integrations": {"slack": body.slack, "discord": body.discord},
        },
    )
    await repos.onboarding.mark_step(org_id, "integrations")
    return {"state": "INTEGRATIONS_CONFIGURED"}


@router.post("/preferences")
async def configure_preferences(
    body: PreferencesRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    _require_transition(current, "PREFERENCES_CONFIGURED", "configure preferences")
    await repos.onboarding.upsert(
        org_id,
        state="PREFERENCES_CONFIGURED",
        selected_repository={
            **_selected(current),
            "preferences": {
                "style": body.style,
                "review_policy": body.review_policy,
                "auto_publish": body.auto_publish,
            },
        },
    )
    await repos.onboarding.mark_step(org_id, "preferences")
    return {"state": "PREFERENCES_CONFIGURED"}


def _redis(request: Request):
    """Redis client or None. Degrades gracefully."""
    redis_client = getattr(request.app.state.draftly, "redis_client", None)
    return redis_client.native if redis_client is not None else None


async def _try_acquire_init_lock(request: Request, org_id: str, run_id: str) -> bool:
    """True if this caller owns the lock (idempotent per run_id)."""
    return await try_acquire_init_lock(_redis(request), org_id, run_id)


async def _release_init_lock(request: Request, org_id: str, run_id: str) -> None:
    """Guarded release shared with the RQ worker via app/services/init_lock."""
    await release_init_lock(_redis(request), org_id, run_id)

def _init_worker_guard(request: Request):
    """Worker + registered-task check; must run BEFORE any state mutation."""
    worker = _worker(request)
    if not worker.task_runner.has_task("onboarding.initialize"):
        raise HTTPException(status_code=404, detail="Unknown job: onboarding.initialize")
    return worker


async def _execute_initialization(
    repos, org_id: str, worker, selected_repository: dict | None, *, request: Request
) -> dict[str, Any]:
    """Kick off the task in the background, return run_id + ticket immediately.

    The frontend needs the run_id + ticket to open an SSE stream BEFORE
    the workflow starts emitting events. Running the task in the background
    ensures the SSE connection is ready to receive stage_change events in
    real time.

    When RQ is enabled (rq_queues + task_handlers on app.state.draftly and
    settings.rq_enabled), the task is enqueued onto the draftly:default
    queue and the in-process worker is skipped. The RQ worker process is
    authoritative for execution and lock release.
    """
    import asyncio
    from uuid import uuid4

    from draftly.app.api.routes.workflows import _tickets

    run_id = f"onboarding-init-{org_id}-{uuid4().hex[:8]}"

    if not await _try_acquire_init_lock(request, org_id, run_id):
        current = await repos.onboarding.get(org_id)
        stored_run_id = (current or {}).get("selected_repository", {}).get("init_run_id")
        # Task 2: reconcile a jobs row for the stored run_id idempotently so
        # /stream-ticket never 404s on a resumed (stale) run.
        if stored_run_id:
            try:
                await repos.jobs.upsert_on_conflict(
                    run_id=stored_run_id,
                    org_id=org_id,
                    name="onboarding.initialize",
                    job_type="onboarding",
                    schedule="manual",
                    configuration={},
                )
                logger.info(
                    "onboarding_jobs_reconciled",
                    run_id=stored_run_id,
                    org_id=org_id,
                )
            except Exception:
                logger.exception(
                    "onboarding_jobs_reconcile_failed",
                    run_id=stored_run_id,
                    org_id=org_id,
                )
                # Never hand out a run_id with no backing jobs row, or the
                # very stream-ticket 404 this task fixes would recur.
                raise HTTPException(
                    status_code=500,
                    detail="Failed to register initialization run",
                ) from None
            return {
                "state": "INITIALIZING",
                "run_id": stored_run_id,
                "resumed": True,
            }
        # Lock is held but there is no run_id to resume (persisted init_run_id
        # was cleared while the owning job never released the lock). This is an
        # orphaned/stale lock — without recovery a fresh start would silently
        # return a null run_id and strand the frontend. Force-release it and
        # acquire for the fresh run below.
        logger.warning(
            "onboarding_stale_init_lock_recovered",
            org_id=org_id,
            run_id=run_id,
        )
        await force_release_init_lock(_redis(request), org_id)
        if not await _try_acquire_init_lock(request, org_id, run_id):
            raise HTTPException(
                status_code=503,
                detail="Initialization already in progress",
            )

    # Guard: the initialize workflow ingests a repo keyed by selected_repository
    # "full_name" (initialize.py reads repo_full = selected_repository["full_name"]).
    # If it's missing, the job would call GitHub with an empty repo path and fail
    # with a cryptic 404 (https://api.github.com/repos/). Reject up front so the
    # frontend can recover (re-select a repository) instead of a doomed job.
    repo_full = (selected_repository or {}).get("full_name", "")
    if not repo_full or "/" not in repo_full:
        raise HTTPException(
            status_code=409,
            detail="No repository selected; choose a repository before initializing",
        )

    ticket = await _tickets(request).issue(run_id, org_id=org_id)

    await repos.onboarding.upsert(
        org_id,
        state="INITIALIZING",
        failure=None,
        selected_repository={
            **(selected_repository or {}),
            "init_run_id": run_id,
        },
    )

    # Task 9: dispatch to RQ when enabled, otherwise fall back to the
    # in-process worker (single-process / unit-test mode).
    app_state = request.app.state.draftly
    rq_queues = getattr(app_state, "rq_queues", None)
    task_handlers = getattr(app_state, "task_handlers", None)
    settings = getattr(app_state, "settings", None)
    rq_enabled = (
        bool(getattr(settings, "rq_enabled", False))
        if settings is not None
        else False
    )

    rq_job_id = ""
    if rq_enabled and rq_queues is not None and task_handlers is not None:
        job = enqueue_job(
            queues=rq_queues,
            task_handlers=task_handlers,
            task_name="onboarding.initialize",
            org_id=org_id,
            selected_repository=selected_repository,
            run_id=run_id,
        )
        rq_job_id = getattr(job, "id", "")

    # Task 1: persist the jobs row via the app-wired repository store so
    # /stream-ticket (workflows.py:59) is guaranteed to see it. A failure is
    # fatal — never silently swallow, or the frontend's SSE would 404 forever.
    try:
        # Task 10: use the conflict-tolerant upsert_on_conflict (ON CONFLICT
        # (run_id) DO NOTHING) rather than a plain insert. Two concurrent
        # /initialize calls that converge on the same run_id (one acquires the
        # lock, the other reconciles and inserts first) would otherwise 500 on
        # the run_id UNIQUE constraint. Idempotency keeps both callers 200 and
        # the stream fully backed by a jobs row.
        await repos.jobs.upsert_on_conflict(
            run_id=run_id,
            org_id=org_id,
            name="onboarding.initialize",
            job_type="onboarding",
            schedule="manual",
            configuration={"rq_job_id": rq_job_id},
        )
        logger.info("onboarding_jobs_inserted", run_id=run_id, org_id=org_id)
    except Exception:
        logger.exception(
            "onboarding_jobs_insert_failed", run_id=run_id, org_id=org_id
        )
        raise HTTPException(
            status_code=500, detail="Failed to register initialization run"
        ) from None

    if rq_enabled and rq_queues is not None and task_handlers is not None:
        return {"state": "INITIALIZING", "run_id": run_id, "ticket": ticket}

    # In-process fallback (single-process / unit tests / RQ not yet enabled).
    async def _run_background() -> None:
        from draftly.workflows.state import WorkflowState, WorkflowStatus

        try:
            raw = await worker.run_task(
                "onboarding.initialize",
                org_id=org_id,
                selected_repository=selected_repository,
                run_id=run_id,
            )
            if isinstance(raw, WorkflowState):
                if raw.status == WorkflowStatus.FAILED:
                    detail = "; ".join(raw.errors)[:300] or "Initialization workflow failed"
                    await repos.onboarding.upsert(
                        org_id,
                        state="FAILED",
                        failure={"step": "initialization", "detail": detail},
                    )
        except Exception as exc:
            logger.exception(
                "onboarding_initialize_background_failed",
                org_id=org_id,
                run_id=run_id,
            )
            await repos.onboarding.upsert(
                org_id,
                state="FAILED",
                failure={"step": "initialization", "detail": str(exc)[:300]},
            )
        finally:
            await _release_init_lock(request, org_id, run_id)

    asyncio.create_task(_run_background())

    return {"state": "INITIALIZING", "run_id": run_id, "ticket": ticket}


@router.post("/initialize")
async def start_initialization(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = (current or {}).get("state", "NOT_STARTED")
    # Re-run if stuck in INITIALIZING (previous attempt may have timed out).
    if current_state == "INITIALIZING":
        worker = _init_worker_guard(request)
        return await _execute_initialization(
            repos, org_id, worker, _selected(current), request=request
        )
    if current_state != "PREFERENCES_CONFIGURED":
        raise HTTPException(status_code=409, detail=f"Cannot initialize from {current_state}")
    worker = _init_worker_guard(request)
    return await _execute_initialization(
        repos, org_id, worker, _selected(current), request=request
    )


@router.get("/initialize/status")
async def get_initialize_status(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    if not current:
        return {"state": "NOT_STARTED", "stage": None}
    failure = current.get("failure")
    if isinstance(failure, dict) and len(str(failure.get("detail", ""))) > 300:
        failure = {**failure, "detail": str(failure.get("detail"))[:300]}
    return {
        "state": current.get("state"),
        "stage": _selected(current).get("init_stage"),
        "run_id": _selected(current).get("init_run_id"),
        "failure": failure,
        "stage_config": current.get("stage_config"),
    }


@router.post("/initialize/retry")
async def retry_initialize(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    if not current or current.get("state") != "FAILED":
        raise HTTPException(status_code=409, detail="Can only retry from FAILED state")
    worker = _init_worker_guard(request)
    return await _execute_initialization(
        repos, org_id, worker, _selected(current), request=request
    )


@router.post("/complete")
async def complete_onboarding(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Spec §5.2: verify COMPLETED prerequisites; finalize. Idempotent."""
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    # Fake-success guard: completion requires an indexed corpus. Applies to
    # the idempotent path too, so a poisoned COMPLETED row cannot masquerade.
    selected = (current or {}).get("selected_repository") or {}
    if not selected.get("document_count"):
        raise HTTPException(
            status_code=409,
            detail="Cannot complete onboarding; no documents indexed",
        )
    current_state = (current or {}).get("state", "NOT_STARTED")
    if current_state == "COMPLETED":
        return {"state": "COMPLETED"}
    steps = (current or {}).get("completed_steps") or []
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            steps = []
    missing = sorted(REQUIRED_STEPS - set(steps))
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot complete onboarding; missing steps: {', '.join(missing)}",
        )
    await repos.onboarding.upsert(org_id, state="COMPLETED")
    return {"state": "COMPLETED"}
