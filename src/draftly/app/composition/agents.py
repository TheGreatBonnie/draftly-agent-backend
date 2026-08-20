"""
Draftly agent composition.

This module connects the existing Draftly deep agents/subagents
to their scoped tool collections and runtime models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agents.draftly_agent import create_draftly_agent
from agents.subagents import build_subagents
from models import create_model_router_middleware

from .tools import ToolRegistry

logger = logging.getLogger(__name__)

HITL_INTERRUPT_ON: dict[str, Any] = {
    "create_pull_request": {
        "allowed_decisions": ["approve", "edit", "reject"],
    },
    "publish_documentation_change": {
        "allowed_decisions": ["approve", "edit", "reject"],
    },
    "send_slack_message": {
        "allowed_decisions": ["approve", "reject"],
    },
    "send_discord_message": {
        "allowed_decisions": ["approve", "reject"],
    },
}


@dataclass(frozen=True)
class AgentRegistry:
    """
    Runtime registry containing Draftly's root agent and
    existing subagents.
    """

    root: Any
    subagents: list[Any]


def build_agents(
    *,
    models: Any,
    tools: ToolRegistry,
    checkpointer: Any = None,
) -> AgentRegistry:
    """
    Build the existing Draftly agent hierarchy.

    No new agent types are introduced here.
    This function only supplies models and scoped tools.
    """

    subagents = build_subagents(
        models=models,
    )

    interrupt_on = HITL_INTERRUPT_ON if checkpointer is not None else {}

    if checkpointer is not None:
        logger.info("hitl_enabled tools=%s", list(HITL_INTERRUPT_ON.keys()))

    root_agent = create_draftly_agent(
        model=models.reasoning,
        middleware=[
            create_model_router_middleware(
                models.router,
                capability="reasoning",
            ),
        ],
        tools=tools.all_tools,
        subagents=subagents,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on,
    )

    return AgentRegistry(
        root=root_agent,
        subagents=subagents,
    )
