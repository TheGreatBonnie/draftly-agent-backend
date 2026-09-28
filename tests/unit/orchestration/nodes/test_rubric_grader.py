"""The rubric judge must actually see the page evidence.

``OutputEvaluator`` composes its judge prompt from a fixed set of sections and
ignores ``EvaluationData.metadata``, so evidence handed over that way silently
never reaches the judge. These tests assert on the composed prompt, because the
failure mode is invisible from the call site.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from strands_evals.types import EvaluationData

from draftly.evaluation.evaluators.completeness import COMPLETENESS_RUBRIC
from draftly.evaluation.evaluators.groundedness import GROUNDEDNESS_RUBRIC
from draftly.orchestration.nodes.rubric_grader import (
    build_changelog_rubric_grader,
    build_docs_rubric_grader,
)

RUBRIC = GROUNDEDNESS_RUBRIC + "\n\n" + COMPLETENESS_RUBRIC


def _prompt_for(grader, draft: str, evidence: list[dict]) -> str:
    """Grade once and return the exact prompt the judge was handed.

    Only the model call is stubbed: ``evaluate_async`` would otherwise build a
    real Strands ``Agent``. ``grade`` and ``_build_prompt`` both run for real,
    so this still covers how ``grade`` hands the evidence over.
    """
    captured: list[str] = []
    evaluator = grader._evaluator
    compose = evaluator._build_prompt

    async def fake_evaluate_async(case: EvaluationData):
        captured.append(str(compose(case)))
        return [SimpleNamespace(score=0.5, test_pass=False, reason="stubbed")]

    evaluator.evaluate_async = fake_evaluate_async  # type: ignore[method-assign]
    asyncio.new_event_loop().run_until_complete(grader.grade(draft=draft, evidence=evidence))
    return captured[0]


def test_judge_prompt_contains_the_page_evidence() -> None:
    prompt = _prompt_for(
        build_docs_rubric_grader(object(), RUBRIC),
        "# Widgets\n\nCall configure().",
        [{"id": "docs/widgets.md", "topic": "widgets"}],
    )
    assert "docs/widgets.md" in prompt
    assert "widgets" in prompt


def test_judge_prompt_does_not_repeat_the_rubric() -> None:
    prompt = _prompt_for(
        build_docs_rubric_grader(object(), RUBRIC),
        "# Widgets",
        [{"id": "docs/widgets.md", "topic": "widgets"}],
    )
    assert prompt.count("Assess whether the documentation is grounded") == 1


def test_judge_prompt_omits_the_block_when_there_is_no_evidence() -> None:
    prompt = _prompt_for(build_docs_rubric_grader(object(), RUBRIC), "# Widgets", [])
    assert "<Evidence>" not in prompt


def test_changelog_judge_prompt_also_carries_evidence() -> None:
    prompt = _prompt_for(
        build_changelog_rubric_grader(object()),
        "## [v1.0.0] - 2026-01-01",
        [{"id": "docs/widgets.md", "topic": "widgets"}],
    )
    assert "docs/widgets.md" in prompt
