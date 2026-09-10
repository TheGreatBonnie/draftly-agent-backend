"""Centralized Strands agent constructor for every Draftly agent.

Every production ``Agent(...)`` construction must route through
``build_draftly_agent`` so that exactly one ``DraftlySteeringHandler`` is
installed at construction time (never by mutating a built agent). Caller
plugins, interventions, structured output, and extra ``Agent`` options are
preserved; on a disabled runtime the steering handler is a defensive no-op.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from strands import Agent

from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from draftly.steering.handler import (
    DEFAULT_STEERING_JUDGE_PROMPT,
    DraftlySteeringHandler,
    build_isolated_judge,
    build_steering_judge,
)
from draftly.steering.policy import RolePolicy, policy_for


def _optional_judge(
    *,
    runtime: SteeringRuntime,
    model: Any,
    policy: RolePolicy,
) -> Any:
    """Build the optional LLM judge, or None when the flag/policy forbid it.

    The judge is constructed in full isolation: a fresh Strands agent with no
    tools, no plugins, no callback handler, and its own model — never the
    application agent under review (see ``build_isolated_judge``).
    """
    if not runtime.config.llm_enabled or not policy.judge_enabled:
        return None
    judge_agent = build_isolated_judge(
        system_prompt=DEFAULT_STEERING_JUDGE_PROMPT,
        model=model,
    )
    return build_steering_judge(
        judge_agent,
        timeout_seconds=runtime.config.judge_timeout_seconds,
    )


def build_draftly_agent(
    *,
    role: AgentRole | str,
    system_prompt: str,
    model: Any,
    tools: Iterable[Any] = (),
    plugins: Iterable[Any] = (),
    runtime: SteeringRuntime,
    agent_id: str,
    node_id: str,
    structured_output_model: Any | None = None,
    interventions: Iterable[Any] = (),
    **agent_options: Any,
) -> Agent:
    """Build a Strands ``Agent`` with steering installed in the constructor.

    An agent-scoped runtime is derived from ``runtime`` via
    ``runtime.for_agent(...)`` so the handler observes the run identity.
    Exactly one ``DraftlySteeringHandler`` is appended to ``plugins``; on a
    disabled runtime it returns ``Proceed`` for every event and never changes
    behavior. The provided plugin/intervention/structured-output lists are
    preserved and the handler is present in the constructor call — nothing is
    mutated after the ``Agent`` is built.
    """
    policy: RolePolicy = policy_for(role)
    agent_runtime = runtime.for_agent(
        agent_id=agent_id,
        node_id=node_id,
        role=policy.role,
    )
    agent_options.setdefault("agent_id", agent_id)
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=list(tools or ()),
        plugins=[
            *plugins,
            DraftlySteeringHandler(
                runtime=agent_runtime,
                policy=policy,
                judge=_optional_judge(runtime=agent_runtime, model=model, policy=policy),
            ),
        ],
        structured_output_model=structured_output_model,
        interventions=list(interventions or ()),
        **agent_options,
    )
