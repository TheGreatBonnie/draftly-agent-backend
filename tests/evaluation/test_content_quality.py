from draftly.content.models import ContentChannel, ContentVariant
from draftly.evaluation.evaluators.content_quality import evaluate_content, evaluate_content_variant


def variant(**overrides):
    data = {
        "id": "v-1", "package_id": "p-1", "channel": ContentChannel.BLOG,
        "title": "Release", "body": "A grounded update",
        "evidence": [{"source_id": "doc-1"}],
        "evaluation": {
            "groundedness": 0.9, "completeness": 0.8,
            "relevance": 0.8, "channel_fit": 0.8,
        },
    }
    data.update(overrides)
    return ContentVariant(**data)


def test_threshold_boundaries_pass():
    assert evaluate_content_variant(variant())["passed"] is True


def test_missing_evidence_blocks_approval():
    result = evaluate_content([])
    assert result["passed"] is False


def test_low_groundedness_is_reported():
    result = evaluate_content_variant(
        variant(
            evaluation={
                "groundedness": 0.89,
                "completeness": 0.8,
                "relevance": 0.8,
                "channel_fit": 0.8,
            }
        )
    )
    assert result["passed"] is False
    assert "groundedness" in result["issues"][0]
