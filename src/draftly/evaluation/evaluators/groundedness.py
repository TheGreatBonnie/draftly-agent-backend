"""Groundedness evaluator (plan §8.2) — OutputEvaluator with a rubric.

The groundedness LLM judge grades how well the authored documentation stays
faithful to the requested scope and any provided evidence (no invented APIs,
no unsupported claims). It uses a rubric-based ``OutputEvaluator`` (scoring
``actual_output``) rather than the trace-level ``FaithfulnessEvaluator`` from
strands_evals: our live documentation runs do not carry a ``Session``
trajectory, so the trace-parsing judge would always raise instead of scoring
the content.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import OutputEvaluator

GROUNDEDNESS_RUBRIC = (
    "Assess whether the documentation is grounded in the given evidence, scope, and "
    "the actual source context described under <ActualEnvironmentState> — the real PR "
    "diff and changed files for PR runs, or the documentation and evidence content "
    "supplied for issue/support runs — without inventing APIs, endpoints, or behavior "
    "that are not supported. Treat every API, method, parameter, and behavior named in "
    "the documentation as traceable if it appears anywhere in the provided evidence "
    "content, diff, changed files, or referenced source. Any claim must be traceable to "
    "the evidence, the change being documented, the actual diff, or the provided "
    "evidence/content files. Do not require a diff when the run is an issue/support "
    "surface with no diff. Score 0-1 based on groundedness."
)


def build_groundedness_evaluator(
    model: Any = None, uses_environment_state: bool = True
) -> OutputEvaluator:
    """Groundedness LLM judge; ``model=None`` uses the SDK default.

    ``uses_environment_state=True`` feeds the real PR diff into the judge
    prompt so it can confirm authored API/behavior claims against the actual
    source (previously the judge only saw the PR description and flagged real
    methods like ``login_with_oauth()`` as hallucinations).
    """
    return OutputEvaluator(
        name="groundedness",
        rubric=GROUNDEDNESS_RUBRIC,
        model=model,
        uses_environment_state=uses_environment_state,
    )
