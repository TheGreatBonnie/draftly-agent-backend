"""Graph routing conditions.

All conditions are defensive: they guard on node presence before
reading any payload, so a partially-executed graph never raises.
"""

from __future__ import annotations

import json

from strands.multiagent.base import Status
from strands.multiagent.graph import GraphState

from draftly.orchestration.nodes.base import safe_node_data

RECOGNIZED_SURFACES = (
    "pull_request",
    "issues",
    "slack",
    "discord",
    "release",
)


def is_valid_surface(state: GraphState) -> bool:
    """
    Surface guard: verify the task's event_type maps to a recognized surface.

    Surface is known from the normalized event (task JSON), NOT the classifier
    output — the classifier is an LLM agent whose result format is not
    guaranteed JSON. The event_type is authoritative.
    """
    if not isinstance(state.task, str):
        return False
    try:
        task_data = json.loads(state.task)
    except json.JSONDecodeError:
        return False
    return task_data.get("event_type", "").split(".")[0] in RECOGNIZED_SURFACES


def route_to_answer_of(node_id: str = "impact"):
    """Factory: route on the ImpactAnalysis produced by ``node_id``.

    The support graph analyzes the question in its ``triage`` node and
    keeps the free-text solution researcher in ``impact``, so it routes
    with ``route_to_answer_of("triage")``.
    """

    def check(state: GraphState) -> bool:
        if node_id not in state.results:
            return False
        data = safe_node_data(state, node_id)
        return data is not None and data.get("action") == "answer"

    return check


def route_to_update_of(node_id: str = "impact"):
    def check(state: GraphState) -> bool:
        if node_id not in state.results:
            return False
        data = safe_node_data(state, node_id)
        return data is not None and data.get("action") == "update"

    return check


def route_to_create_of(node_id: str = "impact"):
    def check(state: GraphState) -> bool:
        if node_id not in state.results:
            return False
        data = safe_node_data(state, node_id)
        return data is not None and data.get("action") == "create"

    return check


route_to_answer = route_to_answer_of()
route_to_update = route_to_update_of()
route_to_create = route_to_create_of()


def route_to_write_of(node_id: str = "impact"):
    """Factory: route to the fan-out writer node when a write is required."""

    def check(state: GraphState) -> bool:
        data = safe_node_data(state, node_id)
        return data is not None and data.get("action") in ("update", "create")

    return check


route_to_write = route_to_write_of()


def generated(state: GraphState) -> bool:
    """Any of answer/update/create/document has produced output."""
    return any(nid in state.results for nid in ("answer", "update", "create", "document"))


def needs_revision(state: GraphState) -> bool:
    """Evaluate ran and the output did not pass."""
    if "evaluate" not in state.results:
        return False
    data = safe_node_data(state, "evaluate")
    return data is not None and not data["passed"]


def needs_revision_of(*node_ids: str):
    """Revision factory scoped to the generation nodes that actually ran.

    The revise loop must route back ONLY to the node that produced the
    failed draft — otherwise both ``update`` and ``create`` edges fire
    simultaneously when evaluation fails (both conditions would be true),
    executing a duplicate/wrong generation path.
    """

    def check(state: GraphState) -> bool:
        if "evaluate" not in state.results:
            return False
        data = safe_node_data(state, "evaluate")
        if data is None or data["passed"]:
            return False
        return any(nid in state.results for nid in node_ids)

    return check


def _review_verdict(state: GraphState) -> str | None:
    if "review" not in state.results:
        return None
    data = safe_node_data(state, "review")
    if data is not None:
        return str(data.get("verdict") or "")
    # safe_node_data only parses payloads whose structured_output is a
    # pydantic model (model_dump) or whose result carries a message; verdict
    # lives on the structured output directly for test/plain-object results.
    node_result = state.results["review"].result
    structured = getattr(node_result, "structured_output", None)
    if structured is None:
        return None
    return str(getattr(structured, "verdict", "") or "")


def eval_ready(state: GraphState) -> bool:
    """Evaluation may run: the answer path completed, or the review verdict
    accepted the fan-out draft (clean). Gates docs-graph edges so evaluation
    never runs mid-correction."""
    if "answer" in state.results:
        return True
    return _review_verdict(state) == "clean"


def needs_correction(state: GraphState) -> bool:
    """Review found targeted corrections; re-dispatch only corrected tasks."""
    return _review_verdict(state) == "correct"


def review_clean(state: GraphState) -> bool:
    """Review accepted the draft (content-supply gate for document → evaluate)."""
    return _review_verdict(state) == "clean"


def eval_passed(state: GraphState) -> bool:
    """Evaluate ran and the output passed."""
    if "evaluate" not in state.results:
        return False
    data = safe_node_data(state, "evaluate")
    return data is not None and data["passed"]


def all_dependencies_complete(required: list[str]):
    """AND-semantics factory: fire only when every listed node completed."""

    def check(state: GraphState) -> bool:
        return all(
            nid in state.results and state.results[nid].status == Status.COMPLETED
            for nid in required
        )

    return check


def none_and_release(state: GraphState) -> bool:
    """Impact chose 'none' but the event is a release — still need a changelog."""
    if "impact" not in state.results:
        return False
    data = safe_node_data(state, "impact")
    if data is None or data.get("action") != "none":
        return False
    task_data = json.loads(state.task) if isinstance(state.task, str) else {}
    return task_data.get("event_type", "").startswith("release")


def generated_changelog(state: GraphState) -> bool:
    """The changelog node has produced output."""
    return "changelog" in state.results


def changelog_needs_revision(state: GraphState) -> bool:
    """Changelog evaluator ran and the output did not pass."""
    if "changelog_evaluate" not in state.results:
        return False
    data = safe_node_data(state, "changelog_evaluate")
    if data is None or data["passed"]:
        return False
    return "changelog" in state.results


def changelog_eval_passed(state: GraphState) -> bool:
    """Changelog evaluator ran and the output passed."""
    if "changelog_evaluate" not in state.results:
        return False
    data = safe_node_data(state, "changelog_evaluate")
    return data is not None and data["passed"]


def delivery_content_ready(state: GraphState) -> bool:
    """The changelog gate passed, so deliver's content-carrying edges may fire.

    Gates the fan-in ``update``/``create``/``answer``/``changelog → deliver``
    edges. Scheduling fires a node when ANY freshly-completed in-edge's
    condition is satisfied, so without this guard the writer/changelog edges
    would trigger ``deliver`` before the changelog was evaluated. This
    condition stays False for every writer/changelog completion (evaluate has
    not run yet), but is True once ``changelog_evaluate`` has passed — at which
    point ``_build_node_input`` folds those completed results into the deliver
    prompt, and the existing ``changelog_evaluate → deliver`` edge actually
    schedules the node.

    Draft-store gate: ``update``/``create``/``document`` stream file bytes
    through the draft store (never inline JSON), so when a docs writer
    completed the evaluator must have confirmed a sealed generation
    (``has_drafts``) before delivery reads the bodies. The ``answer`` path and
    other surfaces carry inline content and are unaffected; offline fixtures
    (``drafts_repo=None``) omit ``has_drafts`` and behave as before.
    """
    if not changelog_eval_passed(state):
        return False
    if any(nid in state.results for nid in ("update", "create", "document")):
        evaluation = safe_node_data(state, "evaluate")
        if isinstance(evaluation, dict) and evaluation.get("has_drafts") is False:
            return False
    return True


def pull_request_opened(state: GraphState) -> bool:
    """True when the task is a ``pull_request.opened`` event.

    Pure event-type gate — never reads ``state.results`` — so it safely gates
    the ``impact → notify`` edge for any run (release events that route to the
    pull_request surface are excluded).
    """
    if not isinstance(state.task, str):
        return False
    try:
        task_data = json.loads(state.task)
    except json.JSONDecodeError:
        return False
    return task_data.get("event_type") == "pull_request.opened"
