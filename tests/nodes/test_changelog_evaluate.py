"""ChangelogEvaluatorNode validates changelog format and content."""

from __future__ import annotations

import json

import pytest
from strands.agent.agent_result import AgentResult

from draftly.orchestration.nodes.changelog_evaluate import (
    ChangelogEvaluatorNode,
    compute_changelog_quality,
)
from draftly.orchestration.nodes.rubric_grader import RubricGrade


class _FakeChangelogGrader:
    """Deterministic fake that returns configurable reasons for testing."""

    def __init__(self, *, reasons: list[str] | None = None) -> None:
        self._reasons = reasons or ["Missing categories: Deprecated, Removed"]
        self.calls: list[dict] = []

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        self.calls.append({"draft": draft, "evidence": evidence})
        return RubricGrade(score=0.5, passed=False, reasons=list(self._reasons))


class _NoopChangelogGrader:
    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        return RubricGrade()


class _FailingChangelogGrader:
    def __init__(self) -> None:
        self.called = False

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        self.called = True
        raise RuntimeError("LLM unavailable")


def _changelog_blocks(
    raw_markdown: str,
    *,
    version: str = "v1.0.0",
    date: str = "2026-09-04",
) -> list[dict]:
    entry = {
        "version": version,
        "date": date,
        "entries": [{"category": "Added", "text": "Feature"}],
        "raw_markdown": raw_markdown,
    }
    return [
        {"text": "Original Task: task"},
        {"text": "\nInputs from previous nodes:"},
        {"text": "\nFrom changelog:"},
        {"text": f"  - changelog_writer: {json.dumps(entry)}"},
    ]


class TestComputeChangelogQuality:
    def test_valid_entry_passes(self) -> None:
        md = "## [v1.0.0] - 2026-09-04\n\n### Added\n- New OAuth support.\n"
        score, reasons = compute_changelog_quality(md)
        assert score >= 0.7
        assert any("format" in r.lower() or "valid" in r.lower() for r in reasons)


class TestChangelogEvaluateLogging:
    @pytest.mark.asyncio
    async def test_changelog_verdict_is_logged(self, monkeypatch) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.orchestration.nodes.changelog_evaluate as node_module

        md = "## [v1.0.0] - 2026-09-04\n\n### Added\n- New OAuth support.\n"

        with capture_logs() as logs:
            monkeypatch.setattr(
                node_module,
                "logger",
                structlog.get_logger("test.changelog.verdict"),
            )
            await ChangelogEvaluatorNode(
                rubric_grader=_NoopChangelogGrader()
            ).invoke_async(_changelog_blocks(md))

        verdict = [line for line in logs if line.get("event") == "changelog_evaluate_verdict"]
        assert len(verdict) == 1
        assert verdict[0]["passed"] is True
        assert verdict[0]["score"] >= 0.7
        assert verdict[0]["iteration"] == 1

    def test_missing_version_header_fails(self) -> None:
        md = "### Added\n- New feature.\n"
        score, reasons = compute_changelog_quality(md)
        assert score < 0.7

    def test_missing_category_header_fails(self) -> None:
        md = "## [v1.0.0] - 2026-09-04\n\n- New feature.\n"
        score, reasons = compute_changelog_quality(md)
        assert score < 0.7

    def test_invalid_category_fails(self) -> None:
        md = "## [v1.0.0] - 2026-09-04\n\n### Improvements\n- Faster.\n"
        score, reasons = compute_changelog_quality(md)
        assert score < 0.7

    def test_non_iso_date_fails(self) -> None:
        md = "## [v1.0.0] - 09/04/2026\n\n### Added\n- Feature.\n"
        score, reasons = compute_changelog_quality(md)
        assert score < 0.7

    def test_empty_markdown_fails(self) -> None:
        score, reasons = compute_changelog_quality("")
        assert score == 0.0

    def test_breaking_change_marked(self) -> None:
        md = (
            "## [v2.0.0] - 2026-09-04\n\n"
            "### Changed\n"
            "- **Breaking:** Removed legacy API endpoint.\n"
        )
        score, reasons = compute_changelog_quality(md)
        assert score >= 0.7
        assert any("breaking" in r.lower() for r in reasons)


class TestChangelogEvaluatorNode:
    @pytest.mark.asyncio
    async def test_passing_entry(self) -> None:
        md = "## [v1.0.0] - 2026-09-04\n\n### Added\n- New OAuth support.\n"
        node = ChangelogEvaluatorNode(rubric_grader=_NoopChangelogGrader())
        result = await node.invoke_async(_changelog_blocks(md))
        node_result = result.results["changelog_evaluate"].result
        assert isinstance(node_result, AgentResult)
        data = json.loads(node_result.message["content"][0]["text"])
        assert data["passed"] is True
        assert data["score"] >= 0.7
        assert data["iteration"] == 1

    @pytest.mark.asyncio
    async def test_failing_entry_then_cap(self) -> None:
        node = ChangelogEvaluatorNode(
            max_iterations=2, rubric_grader=_NoopChangelogGrader()
        )
        blocks = _changelog_blocks("bad format")

        first = await node.invoke_async(blocks)
        first_data = json.loads(
            first.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert first_data["passed"] is False
        assert first_data.get("escalated") is False

        second = await node.invoke_async(blocks)
        second_data = json.loads(
            second.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert second_data["iteration"] == 2
        # Max iterations with an unmet threshold escalates so the graph
        # reaches deliver/ReviewGate instead of burning the node budget.
        assert second_data["passed"] is True
        assert second_data["escalated"] is True
        assert any("escalated" in r.lower() for r in second_data["reasons"])

    @pytest.mark.asyncio
    async def test_empty_changelog_input(self) -> None:
        """No changelog node output -> score 0, still completes."""
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
        ]
        node = ChangelogEvaluatorNode(rubric_grader=_NoopChangelogGrader())
        result = await node.invoke_async(blocks)
        data = json.loads(
            result.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert data["passed"] is False
        assert data["score"] == 0.0


class TestChangelogRubricGraderRequired:
    def test_constructor_requires_a_grader(self) -> None:
        with pytest.raises(TypeError):
            ChangelogEvaluatorNode()
        with pytest.raises(TypeError):
            ChangelogEvaluatorNode(rubric_grader=None)


class TestChangelogRubricGrader:
    @pytest.mark.asyncio
    async def test_grader_reasons_enriched_in_payload(self) -> None:
        """When a failing changelog is graded, the LLM's reasons appear with
        the ``[rubric]`` prefix so the changelog revision pass can act."""
        grader = _FakeChangelogGrader(reasons=["Missing categories: Deprecated, Removed"])
        node = ChangelogEvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_changelog_blocks("bad format"))
        data = json.loads(
            result.results["changelog_evaluate"].result.message["content"][0]["text"]
        )

        assert data["passed"] is False
        rubric_reasons = [r for r in data["reasons"] if r.startswith("[rubric]")]
        assert rubric_reasons
        assert "Missing categories: Deprecated, Removed" in rubric_reasons[0]
        assert len(grader.calls) == 1
        assert grader.calls[0]["draft"] == "bad format"

    @pytest.mark.asyncio
    async def test_grader_not_called_when_entry_passes(self) -> None:
        grader = _FakeChangelogGrader()
        md = "## [v1.0.0] - 2026-09-04\n\n### Added\n- New OAuth support.\n"
        node = ChangelogEvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_changelog_blocks(md))
        data = json.loads(
            result.results["changelog_evaluate"].result.message["content"][0]["text"]
        )

        assert data["passed"] is True
        assert len(grader.calls) == 0

    @pytest.mark.asyncio
    async def test_grader_failure_does_not_crash(self) -> None:
        grader = _FailingChangelogGrader()
        node = ChangelogEvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_changelog_blocks("bad format"))
        data = json.loads(
            result.results["changelog_evaluate"].result.message["content"][0]["text"]
        )

        assert data["passed"] is False
        assert grader.called
        rubric_reasons = [r for r in data["reasons"] if r.startswith("[rubric]")]
        assert rubric_reasons == []
