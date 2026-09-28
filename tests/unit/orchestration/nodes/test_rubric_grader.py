"""The rubric judge must actually see the page evidence.

``OutputEvaluator`` composes its judge prompt from a fixed set of sections and
ignores ``EvaluationData.metadata``, so evidence handed over that way silently
never reaches the judge. These tests assert on the composed prompt, because the
failure mode is invisible from the call site.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
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


# --- the in-graph rubric must stop naming a section it never receives ---------


def test_in_graph_rubric_judge_prompt_has_no_dangling_env_state_reference() -> None:
    """The in-graph graders never set uses_environment_state, so the rubric
    must not tell the judge to read <ActualEnvironmentState>."""
    from draftly.evaluation.evaluators import groundedness

    prompt = _prompt_for(
        build_docs_rubric_grader(
            object(), groundedness.GRAPH_GROUNDEDNESS_RUBRIC + "\n\n" + COMPLETENESS_RUBRIC
        ),
        "# Widgets\n\nCall configure().",
        [{"id": "docs/widgets.md", "topic": "widgets"}],
    )
    assert "docs/widgets.md" in prompt
    assert "ActualEnvironmentState" not in prompt


def test_offline_groundedness_evaluator_still_receives_env_state() -> None:
    """Guard: the offline runner legitimately uses the env-state rubric, and
    this change must not touch it."""
    from draftly.evaluation.evaluators.groundedness import build_groundedness_evaluator

    evaluator = build_groundedness_evaluator(model=None)
    prompt = str(
        evaluator._build_prompt(
            EvaluationData(
                input="",
                actual_output="# Widgets",
                actual_environment_state=[
                    {"name": "review_gate", "state": {"result_status": "INTERRUPTED"}}
                ],
            )
        )
    )
    assert "<ActualEnvironmentState>" in prompt
    assert "INTERRUPTED" in prompt


@pytest.mark.parametrize(
    "module_name",
    ["documentation_graph", "issue_graph", "support_graph"],
)
def test_every_graph_wires_the_in_graph_rubric(module_name) -> None:
    """Wiring guard, not behaviour: all three graphs build the rubric by the
    same expression, and a mechanical swap that misses one would leave the
    dangling reference in place with no behavioural test able to see it."""
    import importlib

    module = importlib.import_module(f"draftly.orchestration.graphs.{module_name}")
    source = Path(inspect.getfile(module)).read_text()
    assert "GRAPH_GROUNDEDNESS_RUBRIC" in source
