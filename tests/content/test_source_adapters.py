from draftly.content.source_adapters import from_content_opportunity, from_github_event
from draftly.feedback.models import ContentOpportunity


def test_github_release_adapter_preserves_evidence():
    request = from_github_event("org-1", {
        "repository_id": "repo-1", "source_event_id": "release-1",
        "source_event_type": "release", "source_title": "1.0",
        "source_summary": "Released", "source_evidence": [{"source_id": "r-1"}],
    })
    assert request.org_id == "org-1"
    assert request.source_evidence == [{"source_id": "r-1"}]


def test_feedback_opportunity_preserves_gap_and_signal_ids():
    request = from_content_opportunity("org-1", ContentOpportunity(
        org_id="org-1", gap_id="gap-1", topic="retries", source_feedback_ids=["f-1"],
        evidence=[{"source_id": "f-1"}],
    ))
    assert request.source_gap_id == "gap-1"
    assert request.source_feedback_ids == ["f-1"]
