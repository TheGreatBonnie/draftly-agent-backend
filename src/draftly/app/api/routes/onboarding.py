"""Onboarding REST API routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token

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
    await repos.onboarding.upsert(
        org_id,
        state="WORKSPACE_CREATED",
        selected_repository={"workspace_name": body.name, "description": body.description},
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


def _init_worker_guard(request: Request):
    """Worker + registered-task check; must run BEFORE any state mutation."""
    worker = _worker(request)
    if not worker.task_runner.has_task("onboarding.initialize"):
        raise HTTPException(status_code=404, detail="Unknown job: onboarding.initialize")
    return worker


async def _execute_initialization(
    repos, org_id: str, worker, selected_repository: dict | None
) -> dict[str, Any]:
    """Flip to INITIALIZING, run the task, and convert any failure to FAILED + 502.

    Accepts both the real worker result (WorkflowState) and plain dicts so
    tests/legacy callers keep working. All post-run shaping stays inside the
    try so nothing after a successful run can strand the row in INITIALIZING.
    """
    from draftly.workflows.state import WorkflowState, WorkflowStatus

    await repos.onboarding.upsert(org_id, state="INITIALIZING", failure=None)
    try:
        raw = await worker.run_task(
            "onboarding.initialize",
            org_id=org_id,
            selected_repository=selected_repository,
        )
        if isinstance(raw, WorkflowState):
            if raw.status == WorkflowStatus.FAILED:
                detail = "; ".join(raw.errors)[:300] or "Initialization workflow failed"
                await repos.onboarding.upsert(
                    org_id,
                    state="FAILED",
                    failure={"step": "initialization", "detail": detail},
                )
                raise HTTPException(status_code=502, detail="Initialization failed")
            return {"state": "COMPLETED", "result": raw.to_dict()}
        result = raw
        return {"state": result.get("state", "COMPLETED"), "result": result}
    except HTTPException:
        raise
    except Exception as exc:
        await repos.onboarding.upsert(
            org_id,
            state="FAILED",
            failure={"step": "initialization", "detail": str(exc)[:300]},
        )
        raise HTTPException(status_code=502, detail="Initialization failed") from exc


@router.post("/initialize")
async def start_initialization(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = _org_id(token)
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = (current or {}).get("state", "NOT_STARTED")
    # Idempotent per spec §5.2: already initializing → report as-is.
    if current_state == "INITIALIZING":
        return {"state": "INITIALIZING"}
    if current_state != "PREFERENCES_CONFIGURED":
        raise HTTPException(status_code=409, detail=f"Cannot initialize from {current_state}")
    worker = _init_worker_guard(request)
    return await _execute_initialization(
        repos, org_id, worker, _selected(current)
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
        "failure": failure,
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
    return await _execute_initialization(repos, org_id, worker, _selected(current))


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
