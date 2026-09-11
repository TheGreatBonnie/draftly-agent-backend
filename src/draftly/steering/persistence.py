"""Steering persistence adapter consumed by ``SteeringRuntime`` sinks.

The policy layer talks to ``runtime.attempts.reserve_tool_guide(...)`` and
``runtime.attempts.reserve_model_guide(...)``. This adapter maps those calls to
durable, atomic ``steering_attempts`` reservations and exposes the durable
intervention inbox for the handler and runner.
"""

from __future__ import annotations

from typing import Any

from draftly.persistence.repositories.steering import (
    AttemptKey,
    InterventionConflictError,
    InterventionNotFoundError,
    InterventionRecord,
    SteeringAttemptsRepository,
    SteeringInterventionsRepository,
)
from draftly.steering.policy import SteeringLimits
from draftly.steering.redaction import redact_value, scrub_secret_values


class SteeringPersistence:
    """Repository-backed steering sinks with bounded default budgets."""

    def __init__(
        self,
        *,
        attempts: SteeringAttemptsRepository,
        interventions: SteeringInterventionsRepository,
        limits: SteeringLimits | None = None,
    ) -> None:
        self.attempts = attempts
        self.interventions = interventions
        self.limits = limits or SteeringLimits()

    async def reserve_tool_guide(
        self,
        *,
        run_id: str,
        agent_id: str,
        node_id: str,
        tool_name: str,
    ) -> bool:
        key = AttemptKey(
            run_id=run_id,
            agent_id=agent_id,
            node_id=node_id,
            phase="tool",
            tool_name=tool_name,
        )
        return await self.attempts.reserve_with_total(
            key=key,
            limit=self.limits.tool_guides_per_call,
            total_key=AttemptKey(
                run_id=run_id,
                agent_id=agent_id,
                node_id="",
                phase="agent_total",
            ),
            total_limit=self.limits.total_guides_per_agent,
        )

    async def reserve_model_guide(
        self,
        *,
        run_id: str,
        agent_id: str,
        node_id: str,
    ) -> bool:
        key = AttemptKey(
            run_id=run_id,
            agent_id=agent_id,
            node_id=node_id,
            phase="model",
        )
        return await self.attempts.reserve_with_total(
            key=key,
            limit=self.limits.model_guides_per_turn,
            total_key=AttemptKey(
                run_id=run_id,
                agent_id=agent_id,
                node_id="",
                phase="agent_total",
            ),
            total_limit=self.limits.total_guides_per_agent,
        )

    async def create_pending(self, *, record: InterventionRecord) -> InterventionRecord:
        return await self.interventions.create_pending(record=record)

    async def claim_response(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
        idempotency_key: str,
        action: str,
        message: str | None,
    ) -> InterventionRecord:
        return await self.interventions.claim_response(
            run_id=run_id,
            interrupt_id=interrupt_id,
            org_id=org_id,
            idempotency_key=idempotency_key,
            action=action,
            message=message,
        )

    async def resolve(
        self,
        *,
        intervention_id: str,
        status: str,
        resolver_id: str | None = None,
    ) -> InterventionRecord:
        return await self.interventions.resolve(
            intervention_id=intervention_id,
            status=status,
            resolver_id=resolver_id,
        )

    async def get_pending(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        org_id: str,
    ) -> InterventionRecord | None:
        return await self.interventions.get_pending(
            run_id=run_id,
            interrupt_id=interrupt_id,
            org_id=org_id,
        )


class SteeringAuditSink:
    """Adapt the steering handler's audit contract to ``AgentRunsRepository``.

    The handler writes ``record_step(run_id, agent_id, node_id, decision,
    tool_name)`` with a ``SteeringDecision``; the run audit repository stores
    structured ``agent_steps`` rows, so each decision maps to one compact
    ``kind='steering'`` step. A ``None`` repository makes the sink a no-op,
    mirroring ``AgentRunsRepository``'s no-database behavior.
    """

    def __init__(self, repo: Any | None) -> None:
        self._repo = repo

    async def record_step(
        self,
        *,
        run_id: str,
        agent_id: str | None = None,
        node_id: str | None = None,
        decision: Any,
        tool_name: str | None = None,
        surface: str = "",
        policy_version: str = "",
        attempt_summary: dict[str, Any] | None = None,
        decision_source: str = "deterministic",
        outcome: str | None = None,
    ) -> None:
        if self._repo is None:
            return
        kind_value = getattr(getattr(decision, "kind", None), "value", None)
        kind = str(kind_value or getattr(decision, "kind", "") or "").lower()
        reason = scrub_secret_values(str(getattr(decision, "reason", "") or ""))[:1_000]
        detail = redact_value(
            {
                "schema_version": "1",
                "decision_type": kind or "unknown",
                "phase": str(getattr(getattr(decision, "phase", None), "value", "") or ""),
                "role": str(getattr(getattr(decision, "role", None), "value", "") or ""),
                "rule": str(getattr(decision, "rule", "") or ""),
                "tool_name": tool_name,
                "reason": reason,
                "surface": surface,
                "policy_version": policy_version,
                "decision_source": decision_source,
                "outcome": outcome or kind or "unknown",
                "interrupt_id": getattr(decision, "interrupt_id", None),
                "attempt_summary": attempt_summary or {},
            },
            max_bytes=4 * 1024,
        )
        if not isinstance(detail, dict):
            detail = {"detail": detail}
        await self._repo.record_step(
            run_id=run_id,
            kind="steering",
            name=kind or "steering",
            status="completed",
            detail=detail,
            agent_id=agent_id,
            node_id=node_id,
            surface=surface,
        )


__all__ = [
    "InterventionConflictError",
    "InterventionNotFoundError",
    "InterventionRecord",
    "SteeringAuditSink",
    "SteeringPersistence",
]
