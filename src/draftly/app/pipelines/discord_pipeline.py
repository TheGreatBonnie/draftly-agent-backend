"""Discord pipeline runner: resolves org, runs workflow, posts reply."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog

from app.config import get_settings
from app.dependencies import build_dependencies
from integrations.shared.org_resolution import get_org_by_discord_guild
from persistence.repositories.discord import save_discord_workflow
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


async def run_discord_pipeline(
    *,
    guild_id: str,
    channel_id: str,
    message_id: str,
    thread_id: str,
    text: str,
    user_id: str,
    app_state: Any,
) -> WorkflowResult | None:
    """
    Orchestrate the Discord pipeline for a support request.

    1. Resolve organization by Discord guild_id
    2. Build WorkflowContext from app_state.dependencies
    3. Run app_state.workflows.discord_support workflow
    4. Save workflow status via save_discord_workflow
    5. Post reply via app_state.integrations.discord.send_thread_message
    """
    try:
        # Resolve organization by Discord guild_id
        deps = build_dependencies(settings=settings)
        org = await get_org_by_discord_guild(
            guild_id=guild_id,
            database=deps.integrations.cockroachdb,
        )

        if not org:
            return WorkflowResult(
                status="failed",
                workflow="discord_support",
                summary=f"Guild '{guild_id}' not linked",
                data={"error": f"Guild '{guild_id}' not linked"},
            )

        org_id = org["clerk_org_id"]

        # Build WorkflowContext
        context = WorkflowContext(
            database=app_state.dependencies.database,
            repositories=app_state.dependencies.repositories,
            memory=app_state.dependencies.memory,
            evaluation=app_state.dependencies.evaluation,
        )

        # Save initial workflow status
        workflow_id = str(uuid4())
        await save_discord_workflow(
            org_id=org_id,
            workflow_id=workflow_id,
            channel_id=channel_id,
            message_id=message_id,
            thread_id=thread_id,
            source_message=text[:2000],
        )

        # Run the workflow
        workflow_result: WorkflowResult = await app_state.workflows.discord_support(
            context,
            channel_id=channel_id,
            message_id=message_id,
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
            return workflow_result

        # Post reply through Discord client
        if workflow_result.status == "completed" and workflow_result.data:
            answer = workflow_result.data.get("answer", {}).get("result", "")
            if answer:
                await app_state.integrations.discord.send_thread_message(
                    thread_id=thread_id,
                    content=answer,
                )

        return workflow_result

    except Exception as e:
        logger.error(
            "discord_pipeline_failed",
            guild_id=guild_id,
            channel_id=channel_id,
            error=str(e),
            exc_info=True,
        )
        # Post error message to Discord
        try:
            target = thread_id or channel_id
            await app_state.integrations.discord.send_message(
                target,
                f"Error processing request: {e}",
            )
        except Exception:
            logger.error("discord_error_reply_failed")
        return None
