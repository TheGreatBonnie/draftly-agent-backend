"""Centralized Strands agent constructor for every Draftly agent.

Every production ``Agent(...)`` construction must route through
``build_draftly_agent`` so that exactly one ``DraftlySteeringHandler`` is
installed at construction time (never by mutating a built agent). Caller
plugins, interventions, structured output, and extra ``Agent`` options are
preserved. A registry guard bounds invented tool retries even when steering is
disabled; on a disabled runtime the steering handler is a defensive no-op.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
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
from draftly.steering.tool_registry_guard import ToolRegistryGuard


@lru_cache(maxsize=1)
def _model_text_logging_enabled() -> bool:
    """Read ``LOG_MODEL_TEXT`` once per process.

    ``get_settings()`` constructs a fresh ``Settings()`` on every call (it is
    not cached), which would re-read the environment for every agent built.
    The import is deferred because this module participates in an import cycle
    with ``agents.documentation``.
    """
    from draftly.app.config import get_settings

    return get_settings().log_model_text


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
    budget: Any | None = None,
    **agent_options: Any,
) -> Agent:
    """Build a Strands ``Agent`` with steering installed in the constructor.

    An agent-scoped runtime is derived from ``runtime`` via
    ``runtime.for_agent(...)`` so the handler observes the run identity.
    Exactly one ``DraftlySteeringHandler`` is appended to ``plugins``; on a
    disabled runtime it returns ``Proceed`` for every event and never changes
    behavior. The provided plugin/intervention/structured-output lists are
    preserved and the handler is present in the constructor call — nothing is
    mutated after the ``Agent`` is built. The registry guard always runs so
    invalid tool names cannot spin through the agent loop, and the GitHub read
    cache plugin is always registered so that any agent in a documented run
    benefits from the cache without opting in.
    """
    policy: RolePolicy = policy_for(role)
    agent_runtime = runtime.for_agent(
        agent_id=agent_id,
        node_id=node_id,
        role=policy.role,
    )
    agent_options.setdefault("agent_id", agent_id)
    # Imported here, not at module scope: the plugin reaches into
    # ``agents.documentation``, whose package ``__init__`` builds agents and so
    # imports this module back. A module-level import would be a cycle.
    from draftly.observability.agent_callbacks import resolve_callback_handler
    from draftly.steering.repo_read_cache_plugin import RepoReadCachePlugin

    # Model reasoning and response text are never logged: Strands' default
    # ``PrintingCallbackHandler`` print()s both to the same fd as structlog,
    # and reasoning deltas arrive with ``end=""`` so they concatenate into one
    # unbounded line -- run d76e2490 produced 2,242 raw lines and 65,520
    # ``<unk>`` tokens. The tool trace is kept, as a structured event, so the
    # diagnostic that b662209's revert was protecting is not lost.
    # ``LOG_MODEL_TEXT=1`` restores the previous behaviour; see
    # ``observability/agent_callbacks.py``. SSE is unaffected either way: it
    # reads ``graph.stream_async``.
    #
    # A caller-supplied handler wins, so an explicit ``callback_handler`` in
    # ``agent_options`` is neither clobbered nor passed twice.
    callback_handler = agent_options.pop(
        "callback_handler",
        resolve_callback_handler(log_model_text=_model_text_logging_enabled()),
    )

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=list(tools or ()),
        plugins=[
            *plugins,
            # Registered on every agent and inert unless a run-scoped cache is
            # installed, so no caller has to opt in.
            RepoReadCachePlugin(),
            DraftlySteeringHandler(
                runtime=agent_runtime,
                policy=policy,
                judge=_optional_judge(runtime=agent_runtime, model=model, policy=policy),
            ),
        ],
        structured_output_model=structured_output_model,
        interventions=[*(interventions or ()), ToolRegistryGuard(), *([budget] if budget else [])],
        callback_handler=callback_handler,
        **agent_options,
    )
