"""Correctness evaluator (plan §8.2) — OutputEvaluator with a rubric.

The correctness LLM judge grades the authored documentation for factual
and technical correctness against the case's evidence/scope. It uses a
rubric-based ``OutputEvaluator`` (scoring ``actual_output``) rather than the
trace-level ``CorrectnessEvaluator`` from strands_evals: our live
documentation runs do not carry a ``Session`` trajectory, so the
trace-parsing judge would always raise instead of scoring the content.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import OutputEvaluator

CORRECTNESS_RUBRIC = (
    "Assess whether the documentation is factually and technically correct with "
    "respect to the requested scope, any provided evidence, and the actual source "
    "context described under <ActualEnvironmentState> — the real PR diff and changed "
    "files for PR runs, or the documentation and evidence content supplied for "
    "issue/support/release runs. Flag inaccuracies, wrong code, incorrect API "
    "references, or misleading guidance. Verify that any named APIs, methods, "
    "parameters, and behavior in the documentation exist in the provided diff or "
    "evidence content before calling them inaccurate. Do not require a diff when the "
    "run is an issue/support/release surface with no diff. A run whose "
    "<ActualEnvironmentState> gate reports result_status INTERRUPTED with deliver_ran "
    "False was paused by the ReviewGate for human review before final delivery — the "
    "authored documentation under evaluation is still complete, so grade the authored "
    "documentation rather than the absence of a delivery act and do not penalize "
    "correctness for the interrupt. Score 0-1 based on correctness."
)


def build_correctness_evaluator(
    model: Any = None, uses_environment_state: bool = True
) -> OutputEvaluator:
    """Correctness LLM judge; ``model=None`` uses the SDK default.

    ``uses_environment_state=True`` feeds the real PR diff (surfaced on
    ``environment_state`` by the online task) into the judge prompt so it can
    verify authored claims against the actual source rather than the thin PR
    description alone (which earlier caused real APIs to be flagged as
    invented).
    """
    return OutputEvaluator(
        name="correctness",
        rubric=CORRECTNESS_RUBRIC,
        model=model,
        uses_environment_state=uses_environment_state,
    )
