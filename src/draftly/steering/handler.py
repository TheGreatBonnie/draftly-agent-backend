"""Strands steering adapter: deterministic policy → Strands actions.

``DraftlySteeringHandler`` subclasses the Strands ``SteeringHandler`` so the
runner can install it via ``init_agent`` hooks. Each callback runs the
deterministic role policy first; an optional judge may only refine a safe
``Proceed``/``Guide`` and can never override a deterministic side-effect
``Interrupt``.

Side effects of steering are durable: every decision is written to
``agent_steps`` as ``kind='steering'`` (bounded, redacted) and every
``Interrupt`` creates a ``workflow_interventions`` row before the Strands
action is returned. Persistence failures fail closed for side-effecting roles
(delivery/reviewer) and open for read-only roles.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, Awaitable, Callable

from strands.vended_plugins.steering import Guide, Interrupt, Proceed, SteeringHandler

from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering.decisions import (
    DecisionKind,
    SteeringDecision,
    SteeringError,
    SteeringFailure,
)
from draftly.steering.policy import RolePolicy


class DraftlySteeringHandler(SteeringHandler):
    """Adapt ``SteeringDecision`` outcomes to Strands steering actions."""

    name = "steering"

    def __init__(
        self,
        *,
        runtime,
        policy: RolePolicy,
        judge: Callable[..., Awaitable[SteeringDecision]] | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.policy = policy
        self.judge = judge
        #: Stable id of the last durable interruption created by this handler.
        self.last_interrupt_id: str | None = None

    # ------------------------------------------------------------------
    # Strands public callbacks (keyword-compatible with the installed SDK)
    # ------------------------------------------------------------------

    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        if not self.runtime.enabled:
            return Proceed(reason="steering disabled")
        try:
            return await self._handle_tool(agent=agent, tool_use=tool_use, **kwargs)
        except SteeringFailure:
            return self._on_persistence_failure()

    async def steer_after_model(self, *, agent, message, stop_reason, **kwargs):
        if not self.runtime.enabled:
            return Proceed(reason="steering disabled")
        try:
            return await self._handle_model(
                agent=agent, message=message, stop_reason=stop_reason, **kwargs
            )
        except SteeringFailure:
            return self._on_persistence_failure(model=True)

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    async def _handle_tool(self, *, agent, tool_use, **kwargs):
        tool_name = (tool_use or {}).get("name", "")
        try:
            decision = await self.policy.evaluate_tool_async(
                runtime=self.runtime,
                tool_name=tool_name,
                tool_use=tool_use,
                judge=self.judge,
            )
        except SteeringError as exc:
            return self._on_policy_failure(exc)
        if decision.kind is DecisionKind.INTERRUPT:
            decision = await self._persist_intervention(decision, tool_name=tool_name)
        await self.record_decision(decision, tool_name=tool_name)
        return decision.to_strands_action()

    async def _handle_model(self, *, agent, message, stop_reason, **kwargs):
        decision = await self.policy.evaluate_model_async(
            runtime=self.runtime,
            message=message,
            stop_reason=stop_reason,
            judge=self.judge,
        )
        await self.record_decision(decision)
        if decision.kind is DecisionKind.INTERRUPT:  # defensive; model never interrupts
            return Guide(reason="model steering cannot interrupt")
        return decision.to_strands_action()

    async def record_decision(
        self, decision: SteeringDecision, *, tool_name: str | None = None
    ) -> None:
        """Write one bounded steering decision to the audit sink.

        Persistence failures raise ``SteeringFailure`` so callers can fail
        closed/open per role.
        """
        audit = self.runtime.audit
        if audit is None:
            return
        identity = self.runtime.identity
        try:
            await audit.record_step(
                run_id=(identity.run_id if identity else self.runtime.scope.run_id),
                agent_id=(identity.agent_id if identity else None),
                node_id=(identity.node_id if identity else None),
                decision=decision,
                tool_name=tool_name,
            )
        except Exception as exc:
            raise SteeringFailure(f"steering audit write failed: {exc}") from exc

    async def _persist_intervention(
        self, decision: SteeringDecision, *, tool_name: str
    ) -> SteeringDecision:
        """Create the durable intervention row before returning ``Interrupt``."""
        interventions = self.runtime.interventions
        if interventions is None:
            return decision
        interrupt_id = decision.interrupt_id or uuid.uuid4().hex
        identity = self.runtime.identity
        record = InterventionRecord(
            run_id=(identity.run_id if identity else self.runtime.scope.run_id),
            interrupt_id=interrupt_id,
            org_id=self.runtime.scope.org_id,
            surface=self.runtime.scope.surface,
            workflow_key=self.runtime.scope.workflow_key,
            agent_id=(identity.agent_id if identity else ""),
            node_id=(identity.node_id if identity else ""),
            tool_name=tool_name,
            status="pending",
            reason={
                "rule": decision.rule,
                "action": decision.kind.value,
                "phase": decision.phase.value,
                "role": decision.role.value if decision.role else None,
            },
            metadata={"surface": self.runtime.scope.surface},
            idempotency_key=f"{self.runtime.scope.org_id}:{interrupt_id}",
        )
        try:
            await interventions.create_pending(record=record)
        except Exception as exc:
            if self.policy.side_effecting:
                raise SteeringFailure(
                    f"intervention persistence failed: {exc}"
                ) from exc
            return decision
        self.last_interrupt_id = interrupt_id
        return replace(decision, interrupt_id=interrupt_id)

    def _on_policy_failure(self, exc: Exception):
        if self.policy.side_effecting:
            return Interrupt(reason=f"{type(exc).__name__}: steering unavailable")
        return Proceed(reason=f"steering unavailable: {type(exc).__name__}")

    def _on_persistence_failure(self, *, model: bool = False):
        if self.policy.side_effecting:
            if model:
                return Guide(reason="steering audit unavailable; side-effecting role fails closed")
            return Interrupt(reason="steering audit unavailable; side-effecting role fails closed")
        return Proceed(reason="steering audit unavailable; read-only role proceeds")