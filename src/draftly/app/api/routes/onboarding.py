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


def _repos(request: Request):
    """Repositories bundle via composition — mirrors routes/documentation.py."""
    return request.app.state.draftly.dependencies.repositories


def _worker(request: Request):
    """Application worker or None — mirrors routes/jobs.py."""
    worker = getattr(request.app.state.draftly, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Background worker is disabled")
    return worker


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
    org_id = token.get("org_id")
    if not isinstance(org_id, str):
        raise HTTPException(status_code=401, detail="Missing organization ID")
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
    org_id = token.get("org_id")
    if not isinstance(org_id, str):
        raise HTTPException(status_code=401, detail="Missing organization ID")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = current["state"] if current else "NOT_STARTED"
    if current_state not in ("NOT_STARTED", "WORKSPACE_CREATED"):
        raise HTTPException(status_code=409, detail=f"Cannot create workspace from {current_state}")
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
    org_id = token.get("org_id")
    if not isinstance(org_id, str):
        raise HTTPException(status_code=401, detail="Missing organization ID")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = current["state"] if current else "NOT_STARTED"
    if current_state not in ("WORKSPACE_CREATED", "GITHUB_CONNECTED"):
        raise HTTPException(status_code=409, detail=f"Cannot connect GitHub from {current_state}")
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
            **((current or {}).get("selected_repository") or {}),
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
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    installation_id = ((current or {}).get("selected_repository") or {}).get("installation_id")
    if not installation_id:
        raise HTTPException(status_code=409, detail="GitHub not connected yet")
    from draftly.integrations.github.app_auth import get_installation_token
    from draftly.integrations.github.client import GitHubClient

    token_value = await get_installation_token(installation_id)
    try:
        github = GitHubClient()
    except RuntimeError:
        from draftly.integrations.github.auth import GitHubAuth

        github = GitHubClient(auth=GitHubAuth(token="installation-token-auth"))
    repositories = await github.get_installation_repositories(token_value)
    return {"repositories": repositories}


@router.post("/repository")
async def select_repository(
    body: RepositoryRequest,
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = current["state"] if current else "NOT_STARTED"
    if current_state not in ("GITHUB_CONNECTED", "REPOSITORY_SELECTED"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot select repository from {current_state}",
        )
    installation_id = ((current or {}).get("selected_repository") or {}).get("installation_id")
    await repos.repository_config.upsert(
        org_id, body.full_name, default_branch=body.default_branch, installation_id=installation_id
    )
    await repos.onboarding.upsert(
        org_id,
        state="REPOSITORY_SELECTED",
        selected_repository={
            **((current or {}).get("selected_repository") or {}),
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
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    repo_full = ((current or {}).get("selected_repository") or {}).get("full_name", "")
    if not repo_full:
        raise HTTPException(status_code=409, detail="Select a repository before discovery")
    from draftly.documentation.discovery import discover_documentation as discover
    from draftly.integrations.github.app_auth import get_installation_token
    from draftly.integrations.github.client import GitHubClient

    installation_id = ((current or {}).get("selected_repository") or {}).get("installation_id")
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
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    repo_full = ((current or {}).get("selected_repository") or {}).get("full_name", "")
    if body.include or body.exclude:
        await repos.repository_config.upsert(
            org_id, repo_full, doc_include=body.include, doc_exclude=body.exclude
        )
        # Mirror into selected_repository so the initialize workflow (which
        # receives that dict) picks up the user's confirmed paths.
        await repos.onboarding.upsert(
            org_id,
            selected_repository={
                **((current or {}).get("selected_repository") or {}),
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
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    await repos.onboarding.upsert(
        org_id,
        state="INTEGRATIONS_CONFIGURED",
        selected_repository={
            **((current or {}).get("selected_repository") or {}),
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
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    await repos.onboarding.upsert(
        org_id,
        state="PREFERENCES_CONFIGURED",
        selected_repository={
            **((current or {}).get("selected_repository") or {}),
            "preferences": {
                "style": body.style,
                "review_policy": body.review_policy,
                "auto_publish": body.auto_publish,
            },
        },
    )
    await repos.onboarding.mark_step(org_id, "preferences")
    return {"state": "PREFERENCES_CONFIGURED"}


async def _run_initialize(
    request: Request, org_id: str, selected_repository: dict | None
) -> dict[str, Any]:
    """Shared initialize path: worker guard + registered task execution."""
    worker = _worker(request)
    if not worker.task_runner.has_task("onboarding.initialize"):
        raise HTTPException(status_code=404, detail="Unknown job: onboarding.initialize")
    return await worker.run_task(
        "onboarding.initialize",
        org_id=org_id,
        selected_repository=selected_repository,
    )


@router.post("/initialize")
async def start_initialization(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = (current or {}).get("state", "NOT_STARTED")
    # Idempotent per spec §5.2: already initializing → report as-is.
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if current_state == "INITIALIZING":
        return {"state": "INITIALIZING"}
    if current_state != "PREFERENCES_CONFIGURED":
        raise HTTPException(status_code=409, detail=f"Cannot initialize from {current_state}")
    await repos.onboarding.upsert(org_id, state="INITIALIZING")
    result = await _run_initialize(request, org_id, (current or {}).get("selected_repository"))
    return {"state": result.get("state", "COMPLETED"), "result": result}


@router.get("/initialize/status")
async def get_initialize_status(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    if not current:
        return {"state": "NOT_STARTED", "stage": None}
    return {
        "state": current.get("state"),
        "stage": ((current or {}).get("selected_repository") or {}).get("init_stage"),
        "failure": current.get("failure"),
    }


@router.post("/initialize/retry")
async def retry_initialize(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    if not current or current.get("state") != "FAILED":
        raise HTTPException(status_code=409, detail="Can only retry from FAILED state")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await repos.onboarding.upsert(org_id, state="INITIALIZING", failure=None)
    result = await _run_initialize(request, org_id, (current or {}).get("selected_repository"))
    return {"state": result.get("state", "COMPLETED"), "result": result}


@router.post("/complete")
async def complete_onboarding(
    request: Request,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Spec §5.2: verify COMPLETED prerequisites; finalize. Idempotent."""
    org_id = token.get("org_id")
    repos = _repos(request)
    current = await repos.onboarding.get(org_id)
    current_state = (current or {}).get("state", "NOT_STARTED")
    if current_state == "COMPLETED":
        return {"state": "COMPLETED"}
    steps = (current or {}).get("completed_steps") or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    missing = sorted(REQUIRED_STEPS - set(steps))
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot complete onboarding; missing steps: {', '.join(missing)}",
        )
    await repos.onboarding.upsert(org_id, state="COMPLETED")
    return {"state": "COMPLETED"}
