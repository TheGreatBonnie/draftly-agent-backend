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
        return await self.attempts.reserve(key=key, limit=self.limits.tool_guides_per_call)

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
        return await self.attempts.reserve(key=key, limit=self.limits.model_guides_per_turn)

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


__all__ = [
    "InterventionConflictError",
    "InterventionNotFoundError",
    "InterventionRecord",
    "SteeringPersistence",
]