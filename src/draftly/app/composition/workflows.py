"""Draftly workflow composition (plan §7.3).

Builds the ``WorkflowRegistry`` with every surface and scheduled
workflow, plus the shared ``WorkflowRunner`` used by webhook routes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .agents import AgentRegistry
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposedWorkflows:
    """The workflow runtime handed to the application lifecycle."""

    registry: Any = None
    runner: Any = None
    context: Any = None
    tasks: dict[str, Any] = field(default_factory=dict)


def build_workflows(
    *,
    agents: AgentRegistry | None = None,
    tools: ToolRegistry | None = None,
    repositories: Any = None,
    memory: Any = None,
    evaluation: Any = None,
    feedback: Any = None,
    config: Any = None,
    model: Any = None,
    hooks: list[Any] | None = None,
    audit_repo: Any = None,
) -> ComposedWorkflows:
    """Compose the workflow registry, context, and runner."""
    del agents  # graphs build agents per-run via build_graph_for_run

    from draftly.workflows.context import WorkflowContext
    from draftly.workflows.documentation.documentation_audit import (
        run_documentation_audit,
    )
    from draftly.workflows.documentation.documentation_sync import (
        run_documentation_sync,
    )
    from draftly.workflows.documentation.github_pr_workflow import (
        run_pull_request_workflow,
    )
    from draftly.workflows.documentation.github_release_workflow import (
        run_release_workflow,
    )
    from draftly.workflows.evaluation.documentation_evaluation import (
        run_evaluation_loop,
    )
    from draftly.workflows.feedback.documentation_feedback_loop import (
        run_feedback_loop,
    )
    from draftly.workflows.github.issue_resolution import (
        run_github_issue_workflow,
    )
    from draftly.workflows.registry import WorkflowRegistry
    from draftly.workflows.runner import WorkflowRunner
    from draftly.workflows.support.discord_support_workflow import (
        run_discord_support,
    )
    from draftly.workflows.support.slack_support_workflow import run_slack_support

    context = WorkflowContext(
        repositories=repositories,
        memory=memory,
        evaluation=evaluation,
        feedback=feedback,
        config=config,
        tools=tools,
        model=model,
        hooks=list(hooks or []),
        audit_repo=audit_repo,
        storage_dir=getattr(
            getattr(config, "strands", None),
            "session_storage_dir",
            ".draftly/sessions",
        ),
    )

    registry = WorkflowRegistry()
    registry.register("github_pr", run_pull_request_workflow)
    registry.register("github_release", run_release_workflow)
    registry.register("github_issue", run_github_issue_workflow)
    registry.register("slack_support", run_slack_support)
    registry.register("discord_support", run_discord_support)
    registry.register("documentation_sync", run_documentation_sync)
    registry.register("documentation_audit", run_documentation_audit)
    registry.register("feedback_loop", run_feedback_loop)
    registry.register("evaluation_loop", run_evaluation_loop)

    runner = WorkflowRunner(context)

    logger.info("workflow registry built workflows=%d", len(registry.names()))

    return ComposedWorkflows(
        registry=registry,
        runner=runner,
        context=context,
    )
