"""HITL review gate evaluators (plan §11): ExpectedInterrupt, ExpectedPassthrough,
and policy calibration matrix.

These tests are fully deterministic — no model keys, no live graph invocation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from draftly.evaluation.runner import (
    ExpectedDelivered,
    ExpectedInterrupt,
    ExpectedPassthrough,
    build_live_evaluators,
)
from draftly.orchestration.routing.policies import is_risky, resolve_review_policy, should_review

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gate_env_state(
    *,
    result_status: str = "COMPLETED",
    had_interrupt: bool = False,
    interrupt_ids: list[str] | None = None,
    deliver_ran: bool = True,
) -> list[dict]:
    return [
        {
            "name": "gate",
            "state": {
                "result_status": result_status,
                "had_interrupt": had_interrupt,
                "interrupt_ids": interrupt_ids or [],
                "deliver_ran": deliver_ran,
            },
        }
    ]


def _case_with_gate(
    name: str,
    expected_gate: str,
    *,
    result_status: str = "COMPLETED",
    had_interrupt: bool = False,
    interrupt_ids: list[str] | None = None,
    deliver_ran: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"expected_gate": expected_gate},
        actual_environment_state=_gate_env_state(
            result_status=result_status,
            had_interrupt=had_interrupt,
            interrupt_ids=interrupt_ids,
            deliver_ran=deliver_ran,
        ),
    )


# ---------------------------------------------------------------------------
# ExpectedInterrupt
# ---------------------------------------------------------------------------


class TestExpectedInterrupt:
    def test_passes_when_interrupted_before_deliver(self) -> None:
        evaluator = ExpectedInterrupt()
        case = _case_with_gate(
            "release-always",
            "interrupt",
            result_status="INTERRUPTED",
            had_interrupt=True,
            interrupt_ids=["v1:before_node_call:deliver:doc-review"],
            deliver_ran=False,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert result.score == 1.0

    def test_fails_when_deliver_ran(self) -> None:
        evaluator = ExpectedInterrupt()
        case = _case_with_gate(
            "release-never",
            "interrupt",
            result_status="COMPLETED",
            had_interrupt=False,
            interrupt_ids=[],
            deliver_ran=True,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert result.score == 0.0
        assert "deliver_ran=True" in result.reason

    def test_fails_when_no_interrupt_ids(self) -> None:
        evaluator = ExpectedInterrupt()
        case = _case_with_gate(
            "release-always",
            "interrupt",
            result_status="INTERRUPTED",
            had_interrupt=True,
            interrupt_ids=[],
            deliver_ran=False,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert "interrupt_ids=[]" in result.reason

    def test_fails_when_no_gate_entry(self) -> None:
        evaluator = ExpectedInterrupt()
        case = SimpleNamespace(
            metadata={"expected_gate": "interrupt"},
            actual_environment_state=[
                {"name": "context", "state": {"delivery_summary": ""}}
            ],
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert "no 'gate' entry" in result.reason

    def test_skips_when_not_expected_gate_interrupt(self) -> None:
        evaluator = ExpectedInterrupt()
        case = SimpleNamespace(metadata={}, actual_environment_state=[])
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert "skipped" in result.reason


# ---------------------------------------------------------------------------
# ExpectedPassthrough
# ---------------------------------------------------------------------------


class TestExpectedPassthrough:
    def test_passes_when_completed_with_delivery(self) -> None:
        evaluator = ExpectedPassthrough()
        case = _case_with_gate(
            "release-never",
            "passthrough",
            result_status="COMPLETED",
            had_interrupt=False,
            interrupt_ids=[],
            deliver_ran=True,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert result.score == 1.0

    def test_fails_when_interrupted(self) -> None:
        evaluator = ExpectedPassthrough()
        case = _case_with_gate(
            "release-always",
            "passthrough",
            result_status="INTERRUPTED",
            had_interrupt=True,
            interrupt_ids=["v1:before_node_call:deliver:doc-review"],
            deliver_ran=False,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert result.score == 0.0
        assert "INTERRUPTED" in result.reason

    def test_fails_when_completed_but_deliver_not_ran(self) -> None:
        evaluator = ExpectedPassthrough()
        case = _case_with_gate(
            "release-none",
            "passthrough",
            result_status="COMPLETED",
            had_interrupt=False,
            interrupt_ids=[],
            deliver_ran=False,
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert "deliver_ran=False" in result.reason

    def test_fails_when_no_gate_entry(self) -> None:
        evaluator = ExpectedPassthrough()
        case = SimpleNamespace(
            metadata={"expected_gate": "passthrough"},
            actual_environment_state=[
                {"name": "context", "state": {"delivery_summary": ""}}
            ],
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert "no 'gate' entry" in result.reason

    def test_skips_when_not_expected_gate_passthrough(self) -> None:
        evaluator = ExpectedPassthrough()
        case = SimpleNamespace(metadata={}, actual_environment_state=[])
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert "skipped" in result.reason


# ---------------------------------------------------------------------------
# ExpectedDelivered + HITL gate interplay
# ---------------------------------------------------------------------------


class TestExpectedDeliveredHitl:
    def test_authoring_case_gated_for_review_passes_without_receipt(self) -> None:
        """Authoring cases with expected_gate='interrupt' need no delivery receipt.

        The review gate halts the graph before the deliver node runs, so a
        receipt can never exist; expected_interrupt asserts the halt instead.
        Not gating on expected_delivered here would require the run to both
        stop before delivery (ExpectedInterrupt) and reach delivery
        (ExpectedDelivered) — a contradiction.
        """
        evaluator = ExpectedDelivered()
        case = SimpleNamespace(
            metadata={
                "expected_action": "update",
                "expected_gate": "interrupt",
            },
            actual_environment_state=[
                {
                    "name": "gate",
                    "state": {
                        "result_status": "INTERRUPTED",
                        "had_interrupt": True,
                        "interrupt_ids": ["v1:before_node_call:deliver:doc-review"],
                        "deliver_ran": False,
                    },
                },
                {"name": "context", "state": {"delivery_summary": ""}},
            ],
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert result.score == 1.0
        assert "human review" in result.reason

    def test_authoring_case_gated_for_review_ignores_delivery_state(self) -> None:
        """Passing for interrupt-gated cases must not depend on the receipt value.

        The receipt is empty (deliver never ran because the gate halted, not
        because authoring failed). A fix that simply inverted the existing
        check would still fail here; expected_interrupt owns delivery assertion.
        """
        evaluator = ExpectedDelivered()
        case = SimpleNamespace(
            metadata={
                "expected_action": "create",
                "expected_gate": "interrupt",
            },
            actual_environment_state=[
                {
                    "name": "gate",
                    "state": {
                        "result_status": "INTERRUPTED",
                        "had_interrupt": True,
                        "interrupt_ids": ["v1:before_node_call:deliver:doc-review"],
                        "deliver_ran": False,
                    },
                },
                {"name": "context", "state": {"delivery_summary": ""}},
            ],
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is True
        assert result.score == 1.0

    def test_authoring_case_without_gate_still_requires_receipt(self) -> None:
        """Non-gated authoring cases keep the delivery-receipt requirement."""
        evaluator = ExpectedDelivered()
        case = SimpleNamespace(
            metadata={"expected_action": "update"},
            actual_environment_state=[
                {"name": "context", "state": {"delivery_summary": ""}},
            ],
        )
        result = evaluator.evaluate(case)[0]
        assert result.test_pass is False
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# build_live_evaluators wiring
# ---------------------------------------------------------------------------


class TestBuildLiveEvaluatorsHitl:
    def test_includes_hitl_evaluators_when_expected_gate_present(self) -> None:
        cases = [
            SimpleNamespace(
                name="release-always",
                input="Release v1.1.0",
                expected_output="changelog",
                metadata={
                    "surface": "release",
                    "expected_gate": "interrupt",
                },
            )
        ]
        evaluators = build_live_evaluators(cases, judge_model=None)
        names = [e.name for e in evaluators]
        assert "expected_interrupt" in names
        assert "expected_passthrough" in names

    def test_excludes_hitl_evaluators_when_no_expected_gate(self) -> None:
        cases = [
            SimpleNamespace(
                name="pr-basic",
                input="PR: add feature",
                expected_output="docs updated",
                metadata={"surface": "pull_request"},
            )
        ]
        evaluators = build_live_evaluators(cases, judge_model=None)
        names = [e.name for e in evaluators]
        assert "expected_interrupt" not in names
        assert "expected_passthrough" not in names

    def test_hitl_evaluators_are_deterministic_no_judge_needed(self) -> None:
        cases = [
            SimpleNamespace(
                name="release-both",
                input="Release",
                expected_output="changelog",
                metadata={
                    "surface": "release",
                    "expected_gate": "interrupt",
                    "expected_action": "update",
                },
            )
        ]
        evaluators = build_live_evaluators(cases, judge_model=None)
        names = [e.name for e in evaluators]
        assert "expected_interrupt" in names
        assert "expected_contains" in names
        assert "expected_authoring_action" in names
        assert "expected_delivered" in names


# ---------------------------------------------------------------------------
# Policy calibration matrix (unit tests for policies.py)
# ---------------------------------------------------------------------------


class TestPolicyCalibration:
    @pytest.mark.parametrize(
        "change_type,urgency,policy,expected",
        [
            ("bug_fix", "medium", "always", True),
            ("bug_fix", "medium", "never", False),
            ("bug_fix", "medium", "risky", False),
            ("breaking_change", "high", "risky", True),
            ("deprecation", "medium", "risky", True),
            ("api_change", "low", "risky", True),
            ("bug_fix", "high", "risky", True),  # high urgency = risky
            ("unknown_type", "low", "always", True),
        ],
    )
    def test_should_review_matrix(
        self, change_type: str, urgency: str, policy: str, expected: bool
    ) -> None:
        classification = {"change_type": change_type, "urgency": urgency}
        assert should_review(policy, classification) == expected

    def test_unknown_policy_defaults_to_always(self) -> None:
        assert resolve_review_policy("unknown") == "always"

    def test_known_policies_preserved(self) -> None:
        assert resolve_review_policy("always") == "always"
        assert resolve_review_policy("never") == "never"
        assert resolve_review_policy("risky") == "risky"

    def test_none_policy_defaults_to_always(self) -> None:
        assert resolve_review_policy(None) == "always"

    def test_is_risky_with_none_classification(self) -> None:
        assert is_risky(None) is True

    def test_is_risky_with_low_risk_classification(self) -> None:
        assert is_risky({"change_type": "bug_fix", "urgency": "low"}) is False

    def test_is_risky_with_high_urgency(self) -> None:
        assert is_risky({"change_type": "bug_fix", "urgency": "high"}) is True

    def test_is_risky_with_breaking_change(self) -> None:
        assert is_risky({"change_type": "breaking_change", "urgency": "low"}) is True


# ---------------------------------------------------------------------------
# Release dataset shape validation
# ---------------------------------------------------------------------------


class TestReleaseDatasetHitlMetadata:
    def test_all_release_cases_have_expected_gate(self) -> None:
        """Every release case must declare expected_gate for HITL evaluation."""
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "draftly"
            / "evaluation"
            / "datasets"
            / "release.json"
        )
        data = json.loads(path.read_text())
        dataset = data[0] if isinstance(data, list) else data
        cases = dataset["cases"]
        for case in cases:
            meta = case["metadata"]
            assert "expected_gate" in meta, f"{case['name']} missing expected_gate"
            assert meta["expected_gate"] in ("interrupt", "passthrough"), (
                f"{case['name']} has invalid expected_gate: {meta['expected_gate']}"
            )
            assert "review_policy" in meta, f"{case['name']} missing review_policy"

    def test_interrupt_cases_use_always_policy(self) -> None:
        """Cases expecting interrupt must use review_policy=always."""
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "draftly"
            / "evaluation"
            / "datasets"
            / "release.json"
        )
        data = json.loads(path.read_text())
        dataset = data[0] if isinstance(data, list) else data
        for case in dataset["cases"]:
            meta = case["metadata"]
            if meta.get("expected_gate") == "interrupt":
                assert meta.get("review_policy") == "always", (
                    f"{case['name']}: interrupt gate requires review_policy=always"
                )

    def test_passthrough_cases_use_never_policy(self) -> None:
        """Cases expecting passthrough must use review_policy=never."""
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "draftly"
            / "evaluation"
            / "datasets"
            / "release.json"
        )
        data = json.loads(path.read_text())
        dataset = data[0] if isinstance(data, list) else data
        for case in dataset["cases"]:
            meta = case["metadata"]
            if meta.get("expected_gate") == "passthrough":
                assert meta.get("review_policy") == "never", (
                    f"{case['name']}: passthrough gate requires review_policy=never"
                )


class TestUniformHitlMetadata:
    """Every gate-capable dataset case must require human review (always/interrupt)."""

    GATE_CAPABLE_DATASETS = (
        "release.json",
        "documentation.json",
        "github_issues.json",
        "support.json",
        "slack.json",
        "discord.json",
    )
    # feedback.json is excluded: the feedback graph has no ReviewGate/deliver node,
    # so expected_gate=interrupt cannot pass there.

    def _load(self, filename: str) -> list[dict]:
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "draftly"
            / "evaluation"
            / "datasets"
            / filename
        )
        data = json.loads(path.read_text())
        return (data[0] if isinstance(data, list) else data)["cases"]

    @pytest.mark.parametrize("filename", GATE_CAPABLE_DATASETS)
    def test_every_case_declares_always_interrupt(self, filename: str) -> None:
        """Every case must set review_policy=always and expected_gate=interrupt."""
        for case in self._load(filename):
            meta = case["metadata"]
            assert meta.get("expected_gate") == "interrupt", (
                f"{case['name']}: expected_gate must be 'interrupt'"
            )
            assert meta.get("review_policy") == "always", (
                f"{case['name']}: review_policy must be 'always' to drive an interrupt gate"
            )
