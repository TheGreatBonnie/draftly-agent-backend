"""Human-in-the-loop review gate: pauses the graph before delivery.

SDK notes (verified against strands-agents 1.52.0):

- Registered as a ``HookProvider`` via ``GraphBuilder.set_hook_providers``
  (``Graph.add_hook`` takes a bare callback, not a provider).
- ``BeforeNodeCallEvent.interrupt(name, reason=...)`` raises
  ``InterruptException`` on first encounter; the graph converts it into an
  interrupt, finishes with ``Status.INTERRUPTED``, and reports the interrupt
  in ``result.interrupts``.
- On resume (``invoke_async([{"interruptResponse": {...}}])``) the callback
  runs again and ``interrupt()`` returns the stored response instead of
  raising — so this gate transparently picks up the reviewer's decision.
- Setting ``event.cancel_node`` to a string makes the SDK cancel the node
  and propagate a ``RuntimeError`` out of ``invoke_async`` (fail-fast).
  Rejection therefore surfaces as an exception, not a FAILED result; the
  workflow runner (Phase 5) must catch it.

Interrupt ids are deterministic (uuid5 of node_id + name), so the id is
stable across processes and session restores.
"""

from __future__ import annotations

import logging
from typing import Any

from strands.hooks import BeforeNodeCallEvent, HookProvider, HookRegistry

from draftly.orchestration.routing.policies import should_review

logger = logging.getLogger(__name__)

REVIEW_NODE_ID = "deliver"
INTERRUPT_NAME = "doc-review"


class ReviewGate(HookProvider):
    """Pause before the delivery node; resume with approval or rejection."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeNodeCallEvent, self.gate)

    def gate(self, event: BeforeNodeCallEvent) -> None:
        if event.node_id != REVIEW_NODE_ID:
            return

        state = event.invocation_state or {}
        policy = state.get("review_policy", "always")
        classification = state.get("classification") or {}

        if not should_review(policy, classification):
            logger.debug(
                "node_id=<%s>, policy=<%s> | review skipped",
                event.node_id,
                policy,
            )
            return

        decision = event.interrupt(
            INTERRUPT_NAME,
            reason={
                "run_id": state.get("run_id"),
                "summary": state.get("delivery_summary", ""),
                "evaluation": state.get("evaluation", {}),
                "evidence_count": state.get("evidence_count", 0),
            },
        )

        if isinstance(decision, dict):
            approved = decision.get("approved") is True
            comment = decision.get("comment", "")
        else:
            approved = bool(decision)
            comment = ""

        if not approved:
            event.cancel_node = f"Rejected by reviewer: {comment}"
