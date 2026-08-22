"""Unit tests for review policies, deterministic classifiers, and hooks."""

from __future__ import annotations

import json

from strands.hooks.registry import HookRegistry
from strands.multiagent.base import MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphState

from draftly.orchestration.hooks.audit import RunAuditLogger
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.base import agent_result
from draftly.orchestration.routing.classifiers import (
    event_prefix,
    surface_for_event,
    workflow_for_event,
)
from draftly.orchestration.routing.conditions import needs_revision_of
from draftly.orchestration.routing.policies import (
    is_risky,
    resolve_review_policy,
    should_review,
)


class TestReviewPolicies:
    def test_resolve_unknown_policy_defaults_to_always(self) -> None:
        assert resolve_review_policy("always") == "always"
        assert resolve_review_policy("risky") == "risky"
        assert resolve_review_policy("never") == "never"
        assert resolve_review_policy(None) == "always"
        assert resolve_review_policy("yolo") == "always"

    def test_should_review_by_policy(self) -> None:
        assert should_review("always", {}) is True
        assert should_review("never", None) is False

    def test_risky_policy_uses_classification(self) -> None:
        breaking = {"change_type": "breaking_change", "urgency": "low"}
        urgent = {"change_type": "bug_fix", "urgency": "high"}
        calm = {"change_type": "bug_fix", "urgency": "low"}

        assert should_review("risky", breaking) is True
        assert should_review("risky", urgent) is True
        assert should_review("risky", calm) is False

    def test_missing_classification_is_treated_as_risky(self) -> None:
        assert is_risky(None) is True
        assert is_risky({}) is True


class TestDeterministicClassifiers:
    def test_event_prefix(self) -> None:
        assert event_prefix("pull_request.synchronize") == "pull_request"
        assert event_prefix("issues.opened") == "issues"
        assert event_prefix("") == ""

    def test_surface_mapping(self) -> None:
        assert surface_for_event("pull_request.opened") == "pull_request"
        assert surface_for_event("issues.opened") == "issue"
        assert surface_for_event("slack.message") == "support"
        assert surface_for_event("discord.message") == "support"
        assert surface_for_event("wiki.deleted") is None

    def test_workflow_mapping(self) -> None:
        assert workflow_for_event("pull_request.opened") == "github_pr"
        assert workflow_for_event("issues.opened") == "github_issue"
        assert workflow_for_event("slack.message") == "support"
        assert workflow_for_event("unknown.event") is None


class TestNeedsRevisionOf:
    def _state(self, evaluate_passed: bool, ran: str) -> GraphState:
        state = GraphState()
        state.task = json.dumps({"event_type": "pull_request.opened"})
        state.results[ran] = NodeResult(
            result=agent_result({"draft": "d"}), status=Status.COMPLETED
        )
        state.results["evaluate"] = NodeResult(
            result=agent_result({"passed": evaluate_passed}),
            status=Status.COMPLETED,
        )
        return state

    def test_routes_only_to_the_node_that_ran(self) -> None:
        to_update = needs_revision_of("update")
        to_create = needs_revision_of("create")

        failed_update_state = self._state(False, "update")
        assert to_update(failed_update_state) is True
        assert to_create(failed_update_state) is False

    def test_no_route_when_evaluation_passed(self) -> None:
        check = needs_revision_of("update")
        assert check(self._state(True, "update")) is False

    def test_defensive_when_evaluate_absent(self) -> None:
        state = GraphState()
        state.task = "task"
        assert needs_revision_of("update")(state) is False


class TestHookRegistration:
    def test_review_gate_registers_callback(self) -> None:
        registry = HookRegistry()
        ReviewGate().register_hooks(registry)
        assert registry.has_callbacks()  # non-empty

    def test_audit_logger_registers_all_callbacks(self) -> None:
        registry = HookRegistry()
        RunAuditLogger(audit_repo=None).register_hooks(registry)
        assert registry.has_callbacks()

    def test_audit_logger_noops_without_repo(self) -> None:
        logger = RunAuditLogger(audit_repo=None)
        state = {"run_id": "r-1"}
        # Must not raise without an audit repository
        logger.node_end(type("E", (), {"node_id": "deliver", "invocation_state": state})())
        logger.run_end(type("E", (), {"invocation_state": state})())


def _unused(result: MultiAgentResult) -> MultiAgentResult:  # pragma: no cover
    return result
