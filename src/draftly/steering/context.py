"""Per-run steering runtime and agent identity scopes."""

from dataclasses import dataclass, field
from typing import Any

from draftly.steering.decisions import AgentRole


@dataclass(frozen=True)
class SteeringRuntimeConfig:
    """Snapshot of steering behavior for one run.

    These fields mirror the ``StrandsConfig`` steering knobs and are frozen
    per run so a policy sees a consistent view across the run lifecycle.
    """

    enabled: bool = False
    enforcement_enabled: bool = False
    policy_version: str = "v1"
    llm_enabled: bool = False
    tool_guides_per_call: int = 2
    model_guides_per_turn: int = 2
    total_guides_per_agent: int = 5
    judge_timeout_seconds: float = 10.0
    reason_max_chars: int = 1_000
    payload_max_bytes: int = 4 * 1024


@dataclass(frozen=True)
class AgentIdentity:
    """Stable identity for one application agent instance within a run."""

    run_id: str
    agent_id: str
    node_id: str
    role: AgentRole


@dataclass(frozen=True)
class RuntimeScope:
    """Run-level ancestry shared by every agent runtime in a run."""

    run_id: str
    surface: str
    org_id: str
    project_id: str
    workflow_key: str | None = None
    repo_checkout_root: str | None = None


@dataclass
class SteeringRuntime:
    """One steering runtime per application agent instance.

    ``SteeringRuntime.disabled()`` yields a defensive no-op runtime for
    offline tests and legacy construction sites that have not been wired
    to a real per-run context yet.
    """

    scope: RuntimeScope
    config: SteeringRuntimeConfig
    identity: AgentIdentity | None = None
    attempts: Any = None
    audit: Any = None
    interventions: Any = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @classmethod
    def disabled(cls) -> "SteeringRuntime":
        """Return a safe no-op runtime for offline/legacy usage."""
        return cls(
            scope=RuntimeScope(
                run_id="",
                surface="",
                org_id="",
                project_id="",
            ),
            config=SteeringRuntimeConfig(enabled=False),
        )

    def for_agent(
        self,
        *,
        run_id: str | None = None,
        agent_id: str,
        node_id: str,
        role: AgentRole,
    ) -> "SteeringRuntime":
        """Derive a new agent-scoped runtime without mutating this one.

        The child shares the run scope, config, and sinks of the parent and
        gains a full ``AgentIdentity``.
        """
        effective_run_id = run_id or self.scope.run_id
        return SteeringRuntime(
            scope=self.scope,
            config=self.config,
            identity=AgentIdentity(
                run_id=effective_run_id,
                agent_id=agent_id,
                node_id=node_id,
                role=role,
            ),
            attempts=self.attempts,
            audit=self.audit,
            interventions=self.interventions,
        )

    def with_sinks(
        self,
        *,
        attempts: Any | None = None,
        audit: Any | None = None,
        interventions: Any | None = None,
    ) -> "SteeringRuntime":
        """Return a copy of this runtime with (possibly) replaced sinks.

        Passed ``None`` values keep the current sink, so builders can swap one
        sink without re-deriving identity or scope.
        """
        return SteeringRuntime(
            scope=self.scope,
            config=self.config,
            identity=self.identity,
            attempts=attempts if attempts is not None else self.attempts,
            audit=audit if audit is not None else self.audit,
            interventions=interventions if interventions is not None else self.interventions,
        )

    @classmethod
    def from_context(
        cls,
        *,
        run_id: str,
        surface: str,
        org_id: str,
        project_id: str,
        workflow_key: str | None,
        config: SteeringRuntimeConfig,
        attempts: Any = None,
        audit: Any = None,
        interventions: Any = None,
    ) -> "SteeringRuntime":
        """Build a run-scoped runtime from WorkflowContext-style inputs."""
        return cls(
            scope=RuntimeScope(
                run_id=run_id,
                surface=surface,
                org_id=org_id,
                project_id=project_id,
                workflow_key=workflow_key,
            ),
            config=config,
            attempts=attempts,
            audit=audit,
            interventions=interventions,
        )