"""Task 1: page-workflow contract tests.

Covers path normalization, immutable artifact identity, blocking metric
behavior, and DocumentationWorkflowResult aggregate pass semantics.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.orchestration.page_workflow.models import (
    CrossPageCorrection,
    CrossPageReviewResult,
    DocumentArtifact,
    DocumentationWorkflowResult,
    MetricResult,
    PageEvaluationResult,
    PageStatus,
    RevisionTask,
    normalize_page_id,
)


def _artifact(**overrides) -> DocumentArtifact:
    data = dict(
        page_id="docs/oauth.md",
        path="docs/oauth.md",
        artifact_id="artifact-1",
        version=1,
        content_hash="a" * 64,
        action="update",
        status="sealed",
        content="# OAuth\n\nCurrent behavior.",
    )
    data.update(overrides)
    return DocumentArtifact(**data)


def _evaluation(**overrides) -> PageEvaluationResult:
    data = dict(
        page_id="docs/oauth.md",
        artifact_id="artifact-1",
        version=1,
        content_hash="a" * 64,
        attempt=1,
        status="passed",
        score=0.9,
        metrics=[
            MetricResult(
                name="quality_score",
                score=0.9,
                threshold=0.7,
                passed=True,
                blocking=True,
                reason="passed",
            )
        ],
        revision_feedback=[],
    )
    data.update(overrides)
    return PageEvaluationResult(**data)


def test_normalize_page_id_rejects_escape() -> None:
    assert normalize_page_id("./docs/oauth.md") == "docs/oauth.md"
    for path in ("/etc/passwd", "../README.md", "docs/../../secret"):
        try:
            normalize_page_id(path)
        except ValueError:
            continue
        raise AssertionError(f"accepted unsafe page path: {path}")


def test_workflow_passes_only_when_every_page_passes() -> None:
    artifact = DocumentArtifact(
        page_id="docs/oauth.md",
        path="docs/oauth.md",
        artifact_id="artifact-1",
        version=1,
        content_hash="a" * 64,
        action="update",
        status="sealed",
        content="# OAuth\n\nCurrent behavior.",
    )
    evaluation = PageEvaluationResult(
        page_id=artifact.page_id,
        artifact_id=artifact.artifact_id,
        version=artifact.version,
        content_hash=artifact.content_hash,
        attempt=1,
        status=PageStatus.PASSED,
        score=0.9,
        metrics=[MetricResult(name="quality_score", score=0.9, threshold=0.7, passed=True, blocking=True, reason="passed")],
        revision_feedback=[],
    )
    result = DocumentationWorkflowResult.from_pages([evaluation])
    assert result.passed is True
    assert result.failed_page_ids == []
    assert result.escalated_page_ids == []


def test_artifact_identity_is_immutable_and_validated() -> None:
    artifact = _artifact()
    with pytest.raises(ValidationError):
        artifact.version = 2
    with pytest.raises(ValidationError):
        _artifact(content_hash="not-a-sha256-hex-digest")
    with pytest.raises(ValidationError):
        _artifact(version=0)


def test_metric_result_enforces_score_bounds() -> None:
    MetricResult(
        name="quality_score",
        score=0.55,
        threshold=0.7,
        passed=False,
        blocking=True,
        reason="below threshold",
    )
    with pytest.raises(ValidationError):
        MetricResult(
            name="quality_score",
            score=1.5,
            threshold=0.7,
            passed=False,
            blocking=True,
            reason="out of range",
        )
    with pytest.raises(ValidationError):
        MetricResult(
            name="quality_score",
            score=0.55,
            threshold=-0.1,
            passed=False,
            blocking=True,
            reason="out of range",
        )


def test_revision_task_carries_failed_blocking_metrics_and_feedback() -> None:
    failed = MetricResult(
        name="quality_score",
        score=0.55,
        threshold=0.7,
        passed=False,
        blocking=True,
        reason="missing refresh flow",
    )
    task = RevisionTask(
        page_id="docs/oauth.md",
        path="docs/oauth.md",
        artifact_id="artifact-1",
        artifact_version=1,
        failed_metrics=[failed],
        instructions=["Document the token refresh failure"],
        revision_attempt=1,
    )
    assert task.failed_metrics[0].blocking is True
    assert task.instructions == ["Document the token refresh failure"]
    assert task.revision_attempt == 1


def test_cross_page_review_contracts() -> None:
    correction = CrossPageCorrection(
        code="CROSS_PAGE_CONFLICT",
        page_ids=["docs/oauth.md", "docs/migration.md"],
        reason="pages specify different PKCE requirements",
    )
    review = CrossPageReviewResult(passed=False, corrections=[correction])
    assert review.passed is False
    assert review.corrections == [correction]
    with pytest.raises(ValidationError):
        CrossPageCorrection(code="UNKNOWN_PAGE", page_ids=[], reason="nobody implicated")


def test_workflow_ready_for_review_when_every_page_passed_or_escalated() -> None:
    passed = _evaluation(page_id="docs/oauth.md")
    escalated = _evaluation(
        page_id="docs/migration.md",
        status="awaiting_human_review",
        artifact_id="artifact-2",
        content_hash="b" * 64,
    )
    result = DocumentationWorkflowResult.from_pages([passed, escalated])
    assert result.passed is False
    assert result.ready_for_review is True
    assert result.escalated_page_ids == ["docs/migration.md"]
    assert result.failed_page_ids == []


def test_workflow_aggregates_revision_required_pages_as_not_ready() -> None:
    passed = _evaluation(page_id="docs/oauth.md")
    revising = _evaluation(
        page_id="docs/migration.md",
        status="revision_required",
        artifact_id="artifact-2",
        content_hash="b" * 64,
    )
    result = DocumentationWorkflowResult.from_pages([passed, revising])
    assert result.passed is False
    assert result.ready_for_review is False
    assert result.failed_page_ids == ["docs/migration.md"]
    assert result.escalated_page_ids == []