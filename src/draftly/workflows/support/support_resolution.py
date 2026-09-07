"""Support resolution logic (plan §7.2).

Post-delivery bookkeeping for support threads: mark the thread resolved
when the graph delivered an answer, or leave it open for the feedback
loop when the run was interrupted/failed.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

import structlog

from draftly.delivery.models import SupportDeliveryReceipt
from draftly.workflows.context import WorkflowContext
from draftly.workflows.state import WorkflowState, WorkflowStatus
from draftly.workflows.support.models import DocumentationGapRequest

logger = structlog.get_logger(__name__)


class SupportGithubTargetError(RuntimeError):
    """No resolvable GitHub repository target for the documentation gap."""


class ReviewGateNotApprovedError(RuntimeError):
    """The review gate has not approved the support delivery."""


def _tool_name(tool: Any) -> str:
    return (
        getattr(tool, "name", None)
        or getattr(getattr(tool, "fn", None), "__name__", None)
        or getattr(tool, "tool_name", None)
        or getattr(tool, "__name__", None)
        or ""
    )


def _named_tools(tools: list[Any]) -> dict[str, Any]:
    return {_tool_name(tool): tool for tool in tools or []}


def _invokable(tool: Any) -> Any:
    return getattr(tool, "fn", None) or getattr(tool, "__wrapped__", None) or tool


def route_support_outcome(
    request: DocumentationGapRequest,
) -> Literal["slack", "discord", "github"]:
    """Route a support outcome to its delivery surface.

    Direct answers return to the originating thread (``slack``/``discord``
    decided by the source in the support graph). Documentation gaps always
    route to the GitHub documentation-delivery path as a reviewed PR.
    """
    return "github"


async def support_to_github(
    request: DocumentationGapRequest,
    registry: Any,
    *,
    approved: bool = False,
) -> dict[str, Any]:
    """Deliver a documentation gap as a reviewed GitHub PR.

    Uses ``registry.github_delivery`` tools (``create_branch``,
    ``create_commit``, ``create_pull_request``) only when a repository target
    is resolvable and the review gate approves.
    """
    if not approved:
        raise ReviewGateNotApprovedError(
            "support_to_github blocked: review gate has not approved delivery"
        )
    if not request.repository or not request.base_sha:
        raise SupportGithubTargetError(
            f"no GitHub delivery target for org={request.org_id!r} "
            f"repository={request.repository!r}"
        )

    owner, _, repo = request.repository.partition("/")
    branch = f"draftly/docs-{uuid4().hex[:8]}"
    files = (request.change_plan or {}).get("files") or []
    body = "\n".join(
        [
            "## Summary",
            "Documentation gap raised from support feedback.",
            "## Source",
            f"Support event(s): {', '.join(request.source_event_ids) or '-'}",
            "## Changes",
            *[f"- {item.get('path')}" for item in files if isinstance(item, dict)],
        ]
    )

    tools = _named_tools(getattr(registry, "github_delivery", []))
    create_branch = tools.get("create_branch")
    create_commit = tools.get("create_commit")
    create_pull_request = tools.get("create_pull_request")
    if create_branch is None or create_commit is None or create_pull_request is None:
        raise SupportGithubTargetError(
            "github_delivery registry is missing branch/commit/PR tools"
        )

    callback = _invokable(create_branch)
    await callback(
        owner=owner,
        repo=repo,
        name=branch,
        base_sha=request.base_sha,
    )
    callback = _invokable(create_commit)
    await callback(
        owner=owner,
        repo=repo,
        branch=branch,
        message="docs: address documentation gap from support feedback",
        files=files,
    )
    callback = _invokable(create_pull_request)
    pr = await callback(
        owner=owner,
        repo=repo,
        title="docs: address documentation gap from support feedback",
        body=body,
        head=branch,
        base=request.base_branch,
    )

    reference = pr.get("number") if isinstance(pr, dict) else None
    logger.info(
        "support_github_delivery org=%s repository=%s branch=%s pr=%s",
        request.org_id,
        request.repository,
        branch,
        reference if reference is not None else "-",
    )
    return {
        "surface": "github",
        "delivered_to": request.repository,
        "reference": str(reference) if reference is not None else "",
        "status": "completed",
        "org_id": request.org_id,
        "branch": branch,
    }


async def resolve_support_thread(
    context: WorkflowContext,
    state: WorkflowState,
    *,
    receipt: SupportDeliveryReceipt | None = None,
) -> dict[str, Any]:
    """Update the support thread from a finished support-graph run.

    ``receipt`` is the durably persisted delivery receipt; when present the
    outcome also exposes the provider message id so callers can confirm the
    reply landed before marking the thread resolved.
    """
    del context  # SupportRepository.update lands with §9 review routes
    resolved = state.status is WorkflowStatus.DELIVERED or (
        receipt is not None and receipt.status == "delivered"
    )
    outcome = {
        "run_id": state.run_id,
        "resolved": resolved,
        "status": state.status.value,
    }
    if receipt is not None:
        outcome["receipt_id"] = receipt.provider_message_id or state.run_id
        outcome["provider_message_id"] = receipt.provider_message_id
        outcome["platform"] = receipt.platform
    logger.info(
        "support_resolution run_id=%s resolved=%s receipt=%s",
        state.run_id,
        outcome["resolved"],
        receipt.provider_message_id if receipt is not None else "-",
    )
    return outcome
