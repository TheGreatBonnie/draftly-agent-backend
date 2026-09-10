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
from draftly.steering.handler import DraftlySteeringHandler
from draftly.steering.policy import RolePolicy, policy_for


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
        plugins=[*plugins, DraftlySteeringHandler(runtime=agent_runtime, policy=policy)],
        structured_output_model=structured_output_model,
        interventions=list(interventions or ()),
        **agent_options,
    )
