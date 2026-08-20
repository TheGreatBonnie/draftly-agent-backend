"""
Draftly workflow composition.

This module provides a registry of all Draftly workflow functions.
Workflows are async functions that accept a WorkflowContext and
workflow-specific parameters.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from workflows import (
    escalate_support,
    run_daily_health_check,
    run_discord_support,
    run_documentation_generation,
    run_documentation_health_check,
    run_documentation_review,
    run_documentation_sync,
    run_evaluation_failure_recovery,
    run_evaluation_loop,
    run_github_issue_workflow,
    run_github_support,
    run_pull_request_workflow,
    run_regression_loop,
    run_release_sync,
    run_release_workflow,
    run_repository_sync,
    run_slack_support,
    run_stale_docs_scan,
    run_support_gap_scan,
)
from workflows.scheduled import run_review_expiry

from .agents import AgentRegistry
from .tools import ToolRegistry

WorkflowFunc = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class WorkflowRegistry:
    """
    Registry of all existing Draftly workflows.

    Each attribute is an async function that accepts a WorkflowContext
    and workflow-specific parameters, returning a WorkflowResult.
    """

    documentation_sync: WorkflowFunc
    documentation_generation: WorkflowFunc
    documentation_review: WorkflowFunc
    documentation_health_check: WorkflowFunc

    github_support: WorkflowFunc
    slack_support: WorkflowFunc
    discord_support: WorkflowFunc
    support_escalation: WorkflowFunc

    pull_request: WorkflowFunc
    release: WorkflowFunc
    issue: WorkflowFunc
    repository_sync: WorkflowFunc

    evaluation_loop: WorkflowFunc
    regression_loop: WorkflowFunc
    failure_recovery: WorkflowFunc

    daily_health_check: WorkflowFunc
    stale_docs_scan: WorkflowFunc
    support_gap_scan: WorkflowFunc
    release_sync: WorkflowFunc
    review_expiry: WorkflowFunc


def build_workflows(
    *,
    agents: AgentRegistry,
    tools: ToolRegistry,
    repositories: Any,
    memory: Any,
    evaluation: Any,
) -> WorkflowRegistry:
    """
    Build Draftly's workflow registry.

    Returns a registry of async workflow functions. The workflows
    themselves are imported from the workflows/ package and are
    called with a WorkflowContext at runtime.
    """

    return WorkflowRegistry(
        documentation_sync=run_documentation_sync,
        documentation_generation=run_documentation_generation,
        documentation_review=run_documentation_review,
        documentation_health_check=run_documentation_health_check,
        github_support=run_github_support,
        slack_support=run_slack_support,
        discord_support=run_discord_support,
        support_escalation=escalate_support,
        pull_request=run_pull_request_workflow,
        release=run_release_workflow,
        issue=run_github_issue_workflow,
        repository_sync=run_repository_sync,
        evaluation_loop=run_evaluation_loop,
        regression_loop=run_regression_loop,
        failure_recovery=run_evaluation_failure_recovery,
        daily_health_check=run_daily_health_check,
        stale_docs_scan=run_stale_docs_scan,
        support_gap_scan=run_support_gap_scan,
        release_sync=run_release_sync,
        review_expiry=run_review_expiry,
    )
