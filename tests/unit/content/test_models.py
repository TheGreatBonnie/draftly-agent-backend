from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from draftly.content.models import (
    ContentChannel,
    ContentPackage,
    ContentPackageStatus,
    ContentRequest,
    ContentVariant,
)


def make_request(**overrides):
    data = {
        "org_id": "org-1",
        "repository_id": "repo-1",
        "source_event_id": "release-1",
        "source_event_type": "release",
        "source_title": "Version 1.2",
        "source_summary": "A useful release",
        "source_evidence": [{"source_id": "doc-1", "quote": "New API"}],
        "requested_channels": [ContentChannel.BLOG, ContentChannel.LINKEDIN],
        "audience": "developers",
        "tone": "clear",
    }
    data.update(overrides)
    return ContentRequest(**data)


def test_request_requires_grounding_and_normalizes_channels():
    request = make_request()

    assert request.requested_channels == [ContentChannel.BLOG, ContentChannel.LINKEDIN]
    assert request.source_evidence[0]["source_id"] == "doc-1"

    with pytest.raises(ValidationError, match="source_evidence"):
        make_request(source_evidence=[])


def test_feedback_gap_requires_gap_and_feedback_provenance():
    with pytest.raises(ValidationError, match="source_gap_id"):
        make_request(source_event_type="feedback_gap", source_feedback_ids=["f-1"])

    with pytest.raises(ValidationError, match="source_feedback_ids"):
        make_request(source_event_type="feedback_gap", source_gap_id="gap-1")

    request = make_request(
        source_event_type="feedback_gap",
        source_gap_id="gap-1",
        source_feedback_ids=["f-1"],
    )
    assert request.source_gap_id == "gap-1"


def test_x_variant_rejects_body_over_platform_limit():
    with pytest.raises(ValidationError, match="280"):
        ContentVariant(
            id="variant-1",
            package_id="package-1",
            channel=ContentChannel.X,
            title="Launch",
            body="x" * 281,
            evidence=[{"source_id": "doc-1"}],
        )


def test_package_keeps_revision_and_provenance():
    request = make_request()
    package = ContentPackage(
        id="package-1",
        org_id=request.org_id,
        repository_id=request.repository_id,
        source_event_id=request.source_event_id,
        source_event_type=request.source_event_type,
        status=ContentPackageStatus.DRAFT,
        brief=request.source_summary,
        source_evidence=request.source_evidence,
        variants=[],
        workflow_run_id="run-1",
        created_at=datetime.now(UTC),
    )

    assert package.status is ContentPackageStatus.DRAFT
    assert package.workflow_run_id == "run-1"
