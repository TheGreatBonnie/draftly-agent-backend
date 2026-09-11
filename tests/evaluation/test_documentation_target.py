from __future__ import annotations

from draftly.evaluation.documentation_target import evaluate_document_content


def test_document_target_evaluation_returns_percent_score_and_reasons() -> None:
    result = evaluate_document_content("# A useful guide\n\n" + ("detail " * 100))

    assert result["score"] == 30.0
    assert result["passed"] is False
    assert result["metrics"]["length"] > 0
    assert result["evaluated_at"].endswith("+00:00")


def test_document_target_evaluation_can_use_evidence_topics() -> None:
    result = evaluate_document_content(
        "# OAuth\n\n" + ("oauth " * 100),
        evidence=[{"topic": "oauth", "id": "oauth"}],
    )

    assert result["score"] == 100.0
    assert result["passed"] is True
    assert result["metrics"]["evidence_coverage"] == 1.0
