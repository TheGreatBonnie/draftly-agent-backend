"""Slack pipeline runner: resolves org, runs workflow, posts reply in-thread."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog

from app.config import get_settings
from app.dependencies import build_dependencies
from integrations.shared.org_resolution import get_org_by_slack_team
from workflows.result import WorkflowResult

logger = structlog.get_logger()

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


async def run_slack_pipeline(
    *,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    text: str,
    user_id: str,
    app_state: Any,
) -> Any | None:
    """
    Orchestrate the Slack pipeline for a support request.

    1. Resolve organization by Slack team_id
    2. Build WorkflowContext from app_state.dependencies
    3. Run app_state.workflows.slack_support workflow
    4. Save workflow status via save_slack_workflow
    5. Reply in-thread via app_state.integrations.slack.send_thread_message
    """
    try:
        # Resolve organization by Slack team_id
        deps = build_dependencies(settings=settings)
        org = await get_org_by_slack_team(
            team_id=team_id,
            database=deps.integrations.cockroachdb,
        )

        if not org:
            return WorkflowResult(
                status="failed",
                workflow="slack_support",
                summary=f"Team '{team_id}' not linked",
                data={"error": f"Team '{team_id}' not linked"},
            )

        org_id = org["clerk_org_id"]

        # Build WorkflowContext
        context = WorkflowContext(
            database=app_state.dependencies.database,
            repositories=app_state.dependencies.repositories,
            memory=app_state.dependencies.memory,
            evaluation=app_state.dependencies.evaluation,
        )

        # Track workflow ID
        workflow_id = str(uuid4())

        # Save initial workflow status
        from persistence.repositories.slack import save_slack_workflow

        await save_slack_workflow(
            org_id=org_id,
            workflow_id=workflow_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            source_message=text[:2000],
        )

        # Run the workflow
        workflow_result = await app_state.workflows.slack_support(
            context,
            channel_id=channel_id,
            thread_ts=thread_ts,
            question=text,
        )

        # Check for agent interrupts — dispatch review notifications
        if workflow_result.interrupts:
            config = {"configurable": {"thread_id": workflow_result.thread_id}}
            await app_state.review_notification.dispatch(
                org_id=org_id,
                interrupts=workflow_result.interrupts,
                config=config,
            )
            # Update workflow status to pending review
            from persistence.repositories.slack import update_slack_workflow_status

            await update_slack_workflow_status(workflow_id, "pending")
            return workflow_result

        # Update workflow status to completed
        from persistence.repositories.slack import update_slack_workflow_status

        await update_slack_workflow_status(workflow_id, "completed")

        # Reply in-thread through Slack client
        if workflow_result.status == "completed" and workflow_result.data:
            answer = workflow_result.data.get("answer", {}).get("result", "")
            if answer:
                await app_state.integrations.slack.send_thread_message(
                    channel_id=channel_id,
                    text=answer,
                    thread_ts=thread_ts,
                )

        return workflow_result

    except Exception as e:
        logger.error(
            "slack_pipeline_failed",
            team_id=team_id,
            channel_id=channel_id,
            error=str(e),
            exc_info=True,
        )
        # Post error message to Slack
        try:
            from integrations.slack.client import SlackClient

            client = SlackClient()
            await client.send_message(
                channel_id=channel_id,
                text=f"Error processing request: {e}",
                thread_ts=thread_ts,
            )
        except Exception:
            logger.error("slack_error_reply_failed")
        return None
