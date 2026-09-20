"""DocumentationQualityEvaluator: deterministic quality scoring for
authored content.  Blocked-variant cases (metadata.expected_blocked)
are not applicable and must pass with an explicit reason rather than
silently failing at 0.3 (the max achievable score with empty evidence).
"""

from __future__ import annotations

from strands_evals.types.evaluation import EvaluationData

from draftly.evaluation.evaluators.documentation_quality import (
    DocumentationQualityEvaluator,
)
from draftly.orchestration.page_workflow.handlers import compute_page_metrics


def _case(actual_output: str, metadata: dict | None = None) -> EvaluationData:
    return EvaluationData(input="release", actual_output=actual_output, metadata=metadata or {})


def test_blocked_variant_returns_not_applicable() -> None:
    """A case flagged expected_blocked must pass regardless of evidence/draft
    because documentation_quality is structurally unpassable when evidence is
    empty (max 0.3 < 0.70 threshold)."""
    evaluator = DocumentationQualityEvaluator()
    results = evaluator.evaluate(_case(
        "Authoring feedback: blocked.\n- telemetry claim unsupported.",
        metadata={"expected_blocked": True, "evidence": []},
    ))
    assert len(results) == 1
    assert results[0].test_pass is True
    assert results[0].score == 1.0
    reason = (results[0].reason or "").lower()
    assert "n/a" in reason or "blocked" in reason


def test_feedback_surface_is_not_applicable() -> None:
    """Feedback cases produce a gap report (no authored docs, no evidence), so
    doc-quality is structurally unpassable and must pass n/a like the
    expected_blocked skip."""
    evaluator = DocumentationQualityEvaluator()
    results = evaluator.evaluate(_case(
        "2 gaps detected:\n- topic: auth-breaking, count 2",
        metadata={"surface": "feedback", "evidence": []},
    ))
    assert len(results) == 1
    assert results[0].test_pass is True
    assert "not applicable" in results[0].reason


def test_unblocked_case_uses_compute_quality() -> None:
    """A normal (non-blocked) case must still score via compute_quality."""
    evaluator = DocumentationQualityEvaluator()
    results = evaluator.evaluate(_case(
        "RBAC organization scoping was added. API key authentication was removed "
        "in the authorization-model update. Migrate by replacing the API key with a "
        "scoped token and a project_id. This change to configure-client requires "
        "assigning roles under the new model before org scope is enforced.",
        metadata={
            "evidence": [
                {"id": "configure-client", "topic": "rbac"},
                {"id": "authorization-model", "topic": "authentication"},
            ]
        },
    ))
    assert len(results) == 1
    assert results[0].test_pass is True
    assert results[0].score >= 0.6


def test_unblocked_case_empty_evidence_still_scores_low() -> None:
    """Without expected_blocked, a case with empty evidence still scores
    via compute_page_metrics (max 0.3 when draft is long enough)."""
    evaluator = DocumentationQualityEvaluator()
    results = evaluator.evaluate(_case(
        "some output text " * 40,  # >500 chars
        metadata={"evidence": []},
    ))
    assert len(results) == 1
    assert results[0].test_pass is False  # 0.3 < 0.70


def test_ci_evaluator_uses_same_quality_score_and_threshold_as_page_runtime() -> None:
    evidence = [{"id": "src/oauth.py", "topic": "token refresh"}]
    draft = "src/oauth token refresh " + "detail " * 90
    runtime_quality = compute_page_metrics(evidence, draft)[-1]

    result = DocumentationQualityEvaluator().evaluate(
        _case(draft, metadata={"evidence": evidence})
    )[0]

    assert result.score == round(runtime_quality.score, 4)
    assert result.test_pass is runtime_quality.passed
    assert runtime_quality.threshold == 0.70
