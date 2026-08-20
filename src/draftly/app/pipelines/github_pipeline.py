"""GitHub pipeline runner: event → workflow → reply."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflows.result import WorkflowResult

from draftly.app.config import get_settings
from draftly.app.dependencies import build_dependencies
from draftly.persistence.repositories.github import (
    get_org_by_github_org,
    save_github_workflow,
)

settings = get_settings()


@dataclass(slots=True)
class WorkflowContext:
    database: Any
    repositories: Any
    memory: Any
    evaluation: Any


async def _check_interrupts(
    workflow_result: Any,
    org_id: str,
    config: dict,
    notification_service: Any,
) -> bool:
    """Check workflow result for interrupts and dispatch notifications.
    Returns True if interrupts were found (caller should not post reply)."""
    interrupts = getattr(workflow_result, "interrupts", None)
    if not interrupts:
        return False

    await notification_service.dispatch(
        org_id=org_id,
        interrupts=interrupts,
        config=config,
    )
    return True


async def run_github_pipeline(
    *,
    payload: dict[str, Any],
    installation_token: str,
    app_state: Any,
) -> WorkflowResult | None:
    """
    Orchestrate the GitHub pipeline for an issue event.

    1. Resolve organization by GitHub org from payload
    2. Build WorkflowContext from app_state.dependencies
    3. Run github_support workflow
    4. Save workflow status
    5. Post reply via GitHub client
    """
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    if not (issue and repo and installation):
        return WorkflowResult(
            status="failed",
            workflow="github_support",
            summary="Invalid payload",
            data={"error": "Invalid payload"},
        )

    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    github_org = repo.get("owner", {}).get("login", "")
    installation_id = installation.get("id")
    issue_number = issue.get("number")

    if not (owner and repo_name and github_org and installation_id and issue_number):
        return WorkflowResult(
            status="failed",
            workflow="github_support",
            summary="Missing required fields",
            data={"error": "Missing required fields"},
        )

    # Resolve organization by GitHub org
    deps = build_dependencies(settings=settings)
    org = await get_org_by_github_org(
        github_org=github_org,
        db=deps.integrations.cockroachdb,
    )

    if not org:
        return WorkflowResult(
            status="failed",
            workflow="github_support",
            summary=f"Organization '{github_org}' not found",
            data={"error": f"Organization '{github_org}' not found"},
        )

    org_id = org["clerk_org_id"]

    # Build WorkflowContext
    context = WorkflowContext(
        database=app_state.dependencies.database,
        repositories=app_state.dependencies.repositories,
        memory=app_state.dependencies.memory,
        evaluation=app_state.dependencies.evaluation,
    )

    # Run the workflow
    question = f"{issue.get('title', '')}\n\n{issue.get('body', '')}"

    workflow_result: WorkflowResult = await app_state.workflows.github_support(
        context,
        owner=owner,
        repo=repo_name,
        issue_number=issue_number,
        question=question,
    )

    # Check for agent interrupts — dispatch review notifications
    if workflow_result.interrupts:
        config = {"configurable": {"thread_id": workflow_result.thread_id}}
        await app_state.review_notification.dispatch(
            org_id=org_id,
            interrupts=workflow_result.interrupts,
            config=config,
        )
        return workflow_result

    # Save workflow status
    await save_github_workflow(
        org_id=org_id,
        workflow_id=workflow_result.data.get("workflow_id", "") if workflow_result.data else "",
        installation_id=installation.get("id", 0),
        owner=owner,
        repo=repo_name,
        issue_number=issue.get("number", 0),
    )

    # Post reply through GitHub client
    if workflow_result.status == "completed" and workflow_result.data:
        answer = workflow_result.data.get("answer", {}).get("result", "")
        if answer:
            await app_state.integrations.github.post_comment(
                owner=owner,
                repo=repo_name,
                issue_number=issue.get("number", 0),
                body=answer,
            )

    return workflow_result
