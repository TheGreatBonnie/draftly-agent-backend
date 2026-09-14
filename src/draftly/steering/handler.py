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

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from strands.vended_plugins.steering import Guide, Proceed, SteeringHandler

from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _default_metrics
from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering.decisions import (
    DecisionKind,
    SteeringDecision,
    SteeringError,
    SteeringFailure,
    SteeringPhase,
)
from draftly.steering.policy import RolePolicy
from draftly.steering.redaction import redact_value, scrub_secret_values

_metrics: Metrics = _default_metrics
logger = structlog.get_logger(__name__)


def _record_decision_metrics(decision: SteeringDecision, surface: str) -> None:
    """Increment the flat decision-dimension counters (no labels)."""
    _metrics.increment("draftly_steering_decisions_total")
    _metrics.increment(f"draftly_steering_actions_{decision.kind.value}_total")
    _metrics.increment(f"draftly_steering_phases_{decision.phase.value}_total")
    role = decision.role.value if decision.role else "none"
    _metrics.increment(f"draftly_steering_roles_{role}_total")
    _metrics.increment(f"draftly_steering_surfaces_{surface or 'none'}_total")
    if (decision.rule or "").startswith("limit:"):
        _metrics.increment("draftly_steering_guide_limits_total")


def _strands_tool_interrupt_id(tool_use_id: str, tool_name: str) -> str:
    """Stable id matching the interrupt Strands itself will emit.

    The vended SteeringHandler raises the interrupt via
    ``event.interrupt(name=f"steering_input_{tool_name}")`` on
    ``BeforeToolCallEvent``, whose ``_interrupt_id`` scheme is
    ``v1:before_tool_call:{toolUseId}:{uuid5(NAMESPACE_OID, name)}``. Deriving
    the same value here lets the runner correlate a durable
    ``workflow_interventions`` row with ``result.interrupts[].id``.
    """
    if not tool_use_id:
        return uuid.uuid4().hex
    return (
        f"v1:before_tool_call:{tool_use_id}:"
        f"{uuid.uuid5(uuid.NAMESPACE_OID, f'steering_input_{tool_name}')}"
    )


DEFAULT_STEERING_JUDGE_PROMPT = (
    "You are an isolated readiness judge for a documentation automation agent. "
    "Given a bounded summary of a proposed action, choose exactly one of "
    "'proceed' (safe), 'guide' (needs a bounded correction), or 'interrupt' "
    "(needs a human). Never emit secrets or raw tool arguments."
)


class _JudgedSteering(BaseModel):
    """The only structured decision an isolated LLM judge may return."""

    decision: Literal["proceed", "guide", "interrupt"]
    reason: str = Field(default="", max_length=1_000)


_JUDGE_KINDS = {
    "proceed": DecisionKind.PROCEED,
    "guide": DecisionKind.GUIDE,
    "interrupt": DecisionKind.INTERRUPT,
}


class _IsolatedJudge:
    """Strands judge boundary with no tools, plugins, or Draftly steering.

    The wrapped Strands ``Agent`` is constructed with ``callback_handler=None``
    and flat ``tools``/``plugins`` lists, using its own model — never the
    application agent under review. There is no recursive Draftly factory
    call, so a judge can neither steer nor inherit application plugins.
    """

    def __init__(self, *, system_prompt: str, model: Any) -> None:
        from strands import Agent

        self.system_prompt = system_prompt
        self.model = model
        self.tools: list[Any] = []
        self.plugins: list[Any] = []
        self._agent = Agent(
            system_prompt=system_prompt,
            model=model,
            tools=[],
            plugins=[],
            callback_handler=None,
        )

    def __call__(self, prompt, *, structured_output_model=None):
        return self._agent(prompt, structured_output_model=structured_output_model)


def build_isolated_judge(*, system_prompt: str, model: Any) -> Any:
    """Construct the isolated Strands judge agent for optional LLM steering."""
    return _IsolatedJudge(system_prompt=system_prompt, model=model)


def build_steering_judge(
    judge_agent: Any,
    *,
    timeout_seconds: float = 10.0,
) -> Callable[..., Awaitable[SteeringDecision]]:
    """Wrap an isolated Strands judge agent into the policy's async boundary.

    The judge receives a bounded, redacted summary of the base decision and
    must return the ``_JudgedSteering`` schema. A timeout, schema violation, or
    model failure raises ``SteeringFailure`` so the policy falls back to the
    deterministic outcome (a judge may only refine, never veto). Model-phase
    judgments never deteriorate into ``Interrupt``.
    """

    # Serializes the Strands judge agent across every invocation of this
    # adapter: the wrapped agent is not reentrant and concurrent before_tool
    # steering callbacks crashed it via the default ThreadPoolExecutor.
    _judge_lock = asyncio.Lock()

    async def judge(*, decision: SteeringDecision) -> SteeringDecision:
        context = redact_value(
            {
                "phase": str(getattr(decision.phase, "value", "") or ""),
                "role": (
                    str(getattr(decision.role, "value", "") or "")
                    if decision.role
                    else None
                ),
                "rule": decision.rule,
                "reason": scrub_secret_values(str(decision.reason or "")),
            },
            max_bytes=4 * 1024,
        )
        prompt = (
            "Bound this agent decision summary and return your structured "
            f"decision. Summary: {json.dumps(context)}"
        )

        def _invoke() -> Any:
            return judge_agent(prompt, structured_output_model=_JudgedSteering)

        loop = asyncio.get_running_loop()

        async def _judge_once() -> Any:
            # The wrapped Strands judge agent is not reentrant: concurrent
            # before_tool steering callbacks called it in parallel through the
            # default ThreadPoolExecutor and crashed it (~27ms fallback burst).
            # Serialize judge invocations so each runs to completion.
            async with _judge_lock:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, _invoke),
                    timeout=timeout_seconds,
                )

        with _metrics.timer("draftly_steering_judge_latency_ms"):
            try:
                result = await _judge_once()
            except TimeoutError as exc:
                _metrics.increment("draftly_steering_judge_fallbacks_total")
                raise SteeringFailure("steering judge timed out") from exc
            except Exception:
                # Transient: retry once before failing open. Timeouts raise
                # above and are never retried (they already consumed the budget).
                try:
                    result = await _judge_once()
                except TimeoutError as exc2:
                    raise SteeringFailure("steering judge timed out") from exc2
                except Exception as exc2:
                    _metrics.increment("draftly_steering_judge_fallbacks_total")
                    raise SteeringFailure(
                        f"steering judge unavailable: {type(exc2).__name__}"
                    ) from exc2

        judged = getattr(result, "structured_output", None)
        if judged is None or (isinstance(judged, dict) and not judged):
            # A tool-input parse-drop yields an empty dict (or loses the
            # structured output entirely); the judge never produced a schema.
            # Treat it as "no refinement" and keep the deterministic base
            # decision instead of burning a fallback on a non-decision.
            logger.warning(
                "steering_judge_no_refinement",
                phase=decision.phase.value,
                role=decision.role.value if decision.role else None,
                rule=decision.rule,
            )
            return decision
        if not isinstance(judged, _JudgedSteering):
            _metrics.increment("draftly_steering_judge_fallbacks_total")
            raise SteeringFailure("steering judge returned an invalid schema")
        kind = _JUDGE_KINDS.get(judged.decision)
        if kind is None:
            _metrics.increment("draftly_steering_judge_fallbacks_total")
            raise SteeringFailure(
                f"steering judge returned unsupported decision '{judged.decision}'"
            )
        phase = decision.phase
        if kind is DecisionKind.INTERRUPT and phase is SteeringPhase.AFTER_MODEL:
            kind = DecisionKind.GUIDE
        return SteeringDecision(
            kind=kind,
            phase=phase,
            reason=judged.reason,
            role=decision.role,
            rule=f"judge:{judged.decision}",
            interrupt_id=decision.interrupt_id,
        )

    return judge


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
            if self.policy.side_effecting:
                raise
            return self._on_persistence_failure()

    async def steer_after_model(self, *, agent, message, stop_reason, **kwargs):
        if not self.runtime.enabled:
            return Proceed(reason="steering disabled")
        try:
            return await self._handle_model(
                agent=agent, message=message, stop_reason=stop_reason, **kwargs
            )
        except SteeringFailure:
            if self.policy.side_effecting:
                raise
            return self._on_persistence_failure(model=True)

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    _IDEM_RESERVED_KEYS = frozenset(
        {"name", "toolUseId", "tool_use_id", "metadata", "idempotency_key"}
    )

    def _stamp_idempotency_key(self, tool_use: dict) -> None:
        """Stamp a deterministic idempotency key on side-effecting tools.

        Keyed on ``org_id|run_id|tool_name|<sorted json args>`` so identical
        calls within a run collide and honest retries are deduplicated.
        Respects an already-present key.
        """
        tool_name = (tool_use or {}).get("name", "")
        if tool_name not in self.policy.side_effect_tools:
            return
        meta = dict(tool_use.get("metadata") or {})
        if meta.get("idempotency_key"):
            return
        scope = self.runtime.scope
        run_id = getattr(scope, "run_id", None) or ""
        org_id = getattr(scope, "org_id", None) or ""
        args = {
            k: v
            for k, v in tool_use.items()
            if k not in self._IDEM_RESERVED_KEYS
        }
        payload = json.dumps(args, sort_keys=True, default=str)
        meta["idempotency_key"] = hashlib.sha256(
            f"{org_id}|{run_id}|{tool_name}|{payload}".encode()
        ).hexdigest()
        tool_use["metadata"] = meta

    @staticmethod
    def _flatten_tool_use(tool_use) -> dict:
        """Merge provider-nested tool args up to the top level.

        Strands providers vary in shape: Bedrock/Anthropic-style tools carry
        their arguments under an ``input`` key, while flat providers put them
        at the top level. Policy checks read args at the top level, so promote
        the nested ``input`` mapping without clobbering reserved keys.
        """
        flat = dict(tool_use or {})
        raw = flat.get("input")
        if isinstance(raw, dict):
            for key, value in raw.items():
                flat.setdefault(key, value)
        return flat

    async def _handle_tool(self, *, agent, tool_use, **kwargs):
        self._stamp_idempotency_key(tool_use)
        tool_use = self._flatten_tool_use(tool_use)
        tool_name = tool_use.get("name", "")
        tool_use_id = tool_use.get("toolUseId") or ""
        enforcement = self.runtime.config.enforcement_enabled
        try:
            if enforcement:
                decision = await self.policy.evaluate_tool_async(
                    runtime=self.runtime,
                    tool_name=tool_name,
                    tool_use=tool_use,
                    judge=self.judge,
                )
            else:
                decision = await self.policy.evaluate_tool_shadow(
                    runtime=self.runtime,
                    tool_name=tool_name,
                    tool_use=tool_use,
                    judge=self.judge,
                )
        except SteeringError as exc:
            return self._on_policy_failure(exc)
        if decision.kind is DecisionKind.INTERRUPT and enforcement:
            decision = await self._persist_intervention(
                decision, tool_name=tool_name, tool_use_id=tool_use_id
            )
        await self.record_decision(decision, tool_name=tool_name)
        if not enforcement:
            _metrics.increment("draftly_steering_shadow_decisions_total")
            return Proceed(reason="shadow mode: steering not enforced")
        return decision.to_strands_action()

    async def _handle_model(self, *, agent, message, stop_reason, **kwargs):
        enforcement = self.runtime.config.enforcement_enabled
        if enforcement:
            decision = await self.policy.evaluate_model_async(
                runtime=self.runtime,
                message=message,
                stop_reason=stop_reason,
                judge=self.judge,
            )
        else:
            decision = await self.policy.evaluate_model_shadow(
                runtime=self.runtime,
                message=message,
                stop_reason=stop_reason,
                judge=self.judge,
            )
        await self.record_decision(decision)
        if not enforcement:
            _metrics.increment("draftly_steering_shadow_decisions_total")
            return Proceed(reason="shadow mode: steering not enforced")
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
        identity = self.runtime.identity
        _record_decision_metrics(decision, self.runtime.scope.surface)
        decision_source = "judge" if (decision.rule or "").startswith("judge:") else "deterministic"
        logger.info(
            "steering_decision",
            run_id=(identity.run_id if identity else self.runtime.scope.run_id),
            surface=self.runtime.scope.surface,
            policy_version=self.runtime.config.policy_version,
            action=decision.kind.value,
            phase=decision.phase.value,
            role=(decision.role.value if decision.role else None),
            agent_id=(identity.agent_id if identity else None),
            node_id=(identity.node_id if identity else None),
            tool_name=tool_name,
            rule=decision.rule,
            decision_source=decision_source,
            interrupt_id=decision.interrupt_id,
            reason=scrub_secret_values(str(decision.reason or ""))[
                : self.runtime.config.reason_max_chars
            ],
        )
        if audit is not None:
            try:
                await audit.record_step(
                    run_id=(identity.run_id if identity else self.runtime.scope.run_id),
                    agent_id=(identity.agent_id if identity else None),
                    node_id=(identity.node_id if identity else None),
                    decision=decision,
                    tool_name=tool_name,
                    surface=self.runtime.scope.surface,
                    policy_version=self.runtime.config.policy_version,
                    attempt_summary={
                        "tool_guides_per_call": self.runtime.config.tool_guides_per_call,
                        "model_guides_per_turn": self.runtime.config.model_guides_per_turn,
                        "total_guides_per_agent": self.runtime.config.total_guides_per_agent,
                    },
                    decision_source=decision_source,
                    outcome=decision.kind.value,
                )
            except Exception as exc:
                _metrics.increment("draftly_steering_audit_failures_total")
                raise SteeringFailure(f"steering audit write failed: {exc}") from exc
        await self.runtime.emit(decision, tool_name=tool_name)

    async def _persist_intervention(
        self,
        decision: SteeringDecision,
        *,
        tool_name: str,
        tool_use_id: str = "",
    ) -> SteeringDecision:
        """Create the durable intervention row before returning ``Interrupt``."""
        interventions = self.runtime.interventions
        if interventions is None:
            if self.policy.side_effecting:
                raise SteeringFailure("intervention persistence is unavailable")
            logger.warning(
                "steering_intervention_not_persisted",
                reason="unavailable",
                phase=decision.phase.value,
                role=decision.role.value if decision.role else None,
                rule=decision.rule,
            )
            return decision
        interrupt_id = decision.interrupt_id or _strands_tool_interrupt_id(
            tool_use_id, tool_name
        )
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
                "reason": scrub_secret_values(str(decision.reason or ""))[
                    : self.runtime.config.reason_max_chars
                ],
                "policy_version": self.runtime.config.policy_version,
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
            logger.warning(
                "steering_intervention_not_persisted",
                reason="failed",
                error=type(exc).__name__,
                phase=decision.phase.value,
                role=decision.role.value if decision.role else None,
                rule=decision.rule,
            )
            return decision
        _metrics.increment("draftly_steering_interrupts_created_total")
        self.last_interrupt_id = interrupt_id
        return replace(decision, interrupt_id=interrupt_id)

    def _on_policy_failure(self, exc: Exception):
        if self.policy.side_effecting:
            raise SteeringFailure(
                f"steering policy unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        logger.warning(
            "steering_policy_unavailable",
            role=self.policy.role.value,
            error=type(exc).__name__,
            mode="open",
        )
        return Proceed(reason=f"steering unavailable: {type(exc).__name__}")

    def _on_persistence_failure(self, *, model: bool = False):
        if self.policy.side_effecting:
            raise SteeringFailure(
                "steering persistence unavailable for side-effecting role"
            )
        logger.warning(
            "steering_audit_unavailable",
            role=self.policy.role.value,
            model=model,
        )
        return Proceed(reason="steering audit unavailable; read-only role proceeds")
