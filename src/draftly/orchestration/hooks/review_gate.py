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

from typing import Any

import structlog
from strands.hooks import BeforeNodeCallEvent, HookProvider, HookRegistry

from draftly.orchestration.nodes.base import safe_node_data
from draftly.orchestration.routing.policies import should_review

logger = structlog.get_logger(__name__)

REVIEW_NODE_ID = "deliver"
INTERRUPT_NAME = "doc-review"

# Writer nodes whose structured output is the document under review,
# in delivery order (first match wins — the winning plan).
WRITER_NODE_IDS = (
    "update",
    "create",
    "answer",
    "content_blog",
    "content_linkedin",
    "content_x",
)


def _classification(source: Any, invocation_state: dict[str, Any]) -> dict[str, Any]:
    """Resolve classification from invocation context or restored graph state."""
    supplied = invocation_state.get("classification")
    if isinstance(supplied, dict) and supplied:
        return supplied

    graph_state = getattr(source, "state", None)
    classified = safe_node_data(graph_state, "classify")
    return classified if isinstance(classified, dict) else {}


def _collect_document(source: Any, state: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the proposed document from completed writer nodes.

    Reads the graph's ``state.results`` (same data the evaluator's edge
    conditions consume via ``safe_node_data``) so the reviewer sees exactly
    what would be delivered. Best-effort: returns ``None`` when no writer
    has completed or payloads did not survive session restore.
    """
    graph_state = getattr(source, "state", None)
    results = getattr(graph_state, "results", None)
    if not isinstance(results, dict):
        return None

    for node_id in WRITER_NODE_IDS:
        data = safe_node_data(graph_state, node_id)
        if not data:
            continue
        if node_id in {"content_blog", "content_linkedin", "content_x"}:
            document = {
                "kind": "content_variant",
                "channel": node_id.removeprefix("content_"),
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "evidence": data.get("evidence", []),
                "feedback_ids": data.get("feedback_ids", []),
                "gap_id": data.get("gap_id"),
            }
        elif node_id == "answer":
            content = data.get("content", "")
            document = {
                "kind": "answer",
                "content": content,
                "summary": data.get("summary") or state.get("delivery_summary", ""),
                "sources": data.get("sources", []),
            }
        else:
            files = data.get("files", [])
            document = {
                "kind": "change_plan",
                "files": files,
                "commit_message": data.get("commit_message", ""),
                "summary": data.get("summary") or state.get("delivery_summary", ""),
                "branch": data.get("branch", ""),
                "repository": data.get("repository", ""),
            }
        if document.get("content") or document.get("body") or document.get("files"):
            return document
    return None


class ReviewGate(HookProvider):
    """Pause before the delivery node; resume with approval or rejection."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeNodeCallEvent, self.gate)

    def gate(self, event: BeforeNodeCallEvent) -> None:
        if event.node_id != REVIEW_NODE_ID:
            return

        state = event.invocation_state or {}
        classification = _classification(event.source, state)
        policy = state.get("review_policy", "always")

        if not should_review(policy, classification):
            logger.debug(
                "review_gate_skip",
                run_id=state.get("run_id"),
                node_id=event.node_id,
                policy=policy,
            )
            return

        logger.info(
            "review_gate_pause",
            run_id=state.get("run_id"),
            node_id=event.node_id,
            policy=policy,
        )
        document = _collect_document(event.source, state) or {}
        summary = (
            document.get("summary")
            or document.get("title")
            or str(state.get("delivery_summary") or "")
            or "a documentation review is pending"
        )
        decision = event.interrupt(
            INTERRUPT_NAME,
            reason={
                "run_id": state.get("run_id"),
                "summary": summary,
                "evaluation": safe_node_data(getattr(event.source, "state", None), "evaluate")
                or state.get("evaluation", {}),
                "evidence_count": state.get("evidence_count", 0),
                "document": document or None,
            },
        )

        if isinstance(decision, dict):
            approved = decision.get("approved") is True
            comment = decision.get("comment", "")
        else:
            approved = bool(decision)
            comment = ""

        if not approved:
            logger.info(
                "review_gate_decision",
                run_id=state.get("run_id"),
                approved=False,
                comment=comment,
            )
            event.cancel_node = f"Rejected by reviewer: {comment}"
        else:
            logger.info(
                "review_gate_decision",
                run_id=state.get("run_id"),
                approved=True,
                comment=comment,
            )
