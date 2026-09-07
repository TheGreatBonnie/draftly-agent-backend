"""ChangelogEvaluatorNode validates changelog format and content."""

from __future__ import annotations

import json

import pytest
from strands.agent.agent_result import AgentResult

from draftly.orchestration.nodes.changelog_evaluate import (
    ChangelogEvaluatorNode,
    compute_changelog_quality,
)


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
        node = ChangelogEvaluatorNode()
        result = await node.invoke_async(_changelog_blocks(md))
        node_result = result.results["changelog_evaluate"].result
        assert isinstance(node_result, AgentResult)
        data = json.loads(node_result.message["content"][0]["text"])
        assert data["passed"] is True
        assert data["score"] >= 0.7
        assert data["iteration"] == 1

    @pytest.mark.asyncio
    async def test_failing_entry_then_cap(self) -> None:
        node = ChangelogEvaluatorNode(max_iterations=2)
        blocks = _changelog_blocks("bad format")

        first = await node.invoke_async(blocks)
        first_data = json.loads(
            first.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert first_data["passed"] is False

        second = await node.invoke_async(blocks)
        second_data = json.loads(
            second.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert second_data["passed"] is False
        assert second_data["iteration"] == 2

    @pytest.mark.asyncio
    async def test_empty_changelog_input(self) -> None:
        """No changelog node output -> score 0, still completes."""
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
        ]
        node = ChangelogEvaluatorNode()
        result = await node.invoke_async(blocks)
        data = json.loads(
            result.results["changelog_evaluate"].result.message["content"][0]["text"]
        )
        assert data["passed"] is False
        assert data["score"] == 0.0
