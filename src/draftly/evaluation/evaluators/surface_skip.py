"""Per-surface n/a skip shared by the authored-documentation rubrics.

The feedback surface produces a gap-analysis report (topic clusters + counts
+ a "no gaps detected" verdict), not authored documentation. The relevance,
completeness, and documentation-quality rubrics score documentation against
the user's requested topic, which is structurally unsatisfiable for a report
that deliberately provides no documentation — judging it produced nondeterministic
failures. These rubrics therefore return a not-applicable pass when the case
metadata declares ``surface == "feedback"``, mirroring the expected_blocked skip.
"""

from __future__ import annotations

from typing import Any

from strands_evals.types import EvaluationData, EvaluationOutput


def feedback_skip_output(
    evaluation_case: EvaluationData[Any, Any] | Any,
    label: str,
) -> list[EvaluationOutput] | None:
    """Return an n/a pass when the case is a feedback-surface run, else None.

    The caller (a rubric ``OutputEvaluator``) checks this first and delegates
    to the LLM judge only when ``None`` is returned.
    """
    metadata = getattr(evaluation_case, "metadata", None) or {}
    if metadata.get("surface") != "feedback":
        return None
    return [
        EvaluationOutput(
            score=1.0,
            test_pass=True,
            reason=f"n/a: feedback surface produces a gap report; {label} rubric not applicable",
            label=label,
        )
    ]