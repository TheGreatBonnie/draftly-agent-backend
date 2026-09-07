"""StrandsEvalsRunner with a mocked Experiment (plan §11.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from draftly.evaluation import StrandsEvalsRunner


@dataclass
class FakeReport:
    overall_score: float = 0.5
    test_passes: list = field(default_factory=lambda: [True, False])
    reasons: list = field(default_factory=lambda: ["", "missing section"])
    cases: list = field(default_factory=lambda: [{"name": "a"}, {"name": "b"}])


@dataclass
class FakeExperiment:
    cases: list = field(default_factory=list)
    evaluators: list = field(default_factory=list)
    report: FakeReport = field(default_factory=FakeReport)
    task_calls: list = field(default_factory=list)
    data_store_calls: list = field(default_factory=list)

    async def run_evaluations_async(self, task, evaluation_data_store=None):
        self.task_calls.append(task)
        self.data_store_calls.append(evaluation_data_store)
        return self.report


@pytest.fixture
def mocked_experiment(monkeypatch: pytest.MonkeyPatch):
    created: list[FakeExperiment] = []

    def factory(*, cases, evaluators):
        experiment = FakeExperiment(cases=cases, evaluators=evaluators)
        created.append(experiment)
        return experiment

    monkeypatch.setattr("draftly.evaluation.runner.Experiment", factory)
    return created


async def test_runner_builds_experiment_and_persists(
    mocked_experiment,
) -> None:
    persisted: list[dict] = []

    class Repo:
        async def create(self, **kwargs):
            persisted.append(kwargs)
            return {"id": "eval-1", **kwargs}

    runner = StrandsEvalsRunner(repository=Repo(), org_id="org-9", evaluation_type="documentation")
    from strands_evals import Case

    cases = [
        Case(name="a", input="q1", expected_output="e1"),
        Case(name="b", input="q2", expected_output="e2"),
    ]

    report, record = await runner.run_and_persist(
        cases, [object()], lambda case: case.expected_output
    )

    assert len(mocked_experiment) == 1
    experiment = mocked_experiment[0]
    assert [c.name for c in experiment.cases] == ["a", "b"]
    assert experiment.task_calls  # task callable was passed through

    assert record is not None
    assert report.overall_score == pytest.approx(0.5)
    assert record["org_id"] == "org-9"
    assert record["status"] == "failed"  # one case failed
    assert record["failures"][0]["case"] == "b"


async def test_runner_without_repo_skips_persist(mocked_experiment) -> None:
    from strands_evals import Case

    runner = StrandsEvalsRunner(repository=None)

    report, record = await runner.run_and_persist(
        [Case(name="a", input="q1")], [], lambda case: None
    )

    assert record is None
    assert report.overall_score == pytest.approx(0.5)


async def test_runner_passes_evaluation_data_store_to_strands(mocked_experiment) -> None:
    from strands_evals import Case

    store = object()
    runner = StrandsEvalsRunner(evaluation_data_store=store)

    await runner.run([Case(name="a", input="q1")], [], lambda case: case.input)

    assert mocked_experiment[0].data_store_calls == [store]


async def test_run_dataset_online_builds_cases_with_custom_evaluators(monkeypatch) -> None:
    from draftly.evaluation.runner import run_dataset_online

    created: list[FakeExperiment] = []

    def factory(*, cases, evaluators):
        experiment = FakeExperiment(cases=cases, evaluators=evaluators)
        created.append(experiment)
        return experiment

    monkeypatch.setattr("strands_evals.Experiment", factory)
    evaluator = object()

    await run_dataset_online(
        client=object(),
        dataset={
            "name": "custom",
            "cases": [{"name": "case-1", "input": "question", "expected_output": "answer"}],
        },
        evaluators=[evaluator],
    )

    assert [case.name for case in created[0].cases] == ["case-1"]
    assert created[0].evaluators == [evaluator]


def test_report_rows_surfaces_swallowed_task_errors() -> None:
    """Rows must appear even when the Strands worker records an empty detailed_results.

    Regression: when a live task raises inside the Evals worker, the worker emits
    one failing row per evaluator with ``detailed_results=[]`` and the error text
    only in ``reason``. ``report_rows``/``_report_rows`` iterated ONLY
    ``detailed_results``, producing 0 rows — a silent-zero that the graph then
    reported as ``passed_all=true``.
    """
    from strands_evals.types.evaluation_report import EvaluationReport

    report = EvaluationReport(
        overall_score=0.0,
        scores=[0, 0, 0],
        test_passes=[False, False, False],
        cases=[
            {"name": "oauth-auth", "evaluator": "expected_contains"},
            {"name": "oauth-auth", "evaluator": "expected_tools"},
            {"name": "oauth-auth", "evaluator": "node:context:semantic_search"},
        ],
        reasons=[
            "An error occurred: 'GraphNode' object has no attribute 'get_agent_results'",
            "An error occurred: 'GraphNode' object has no attribute 'get_agent_results'",
            "An error occurred: 'GraphNode' object has no attribute 'get_agent_results'",
        ],
        detailed_results=[[], [], []],
        diagnoses=[None, None, None],
        recommendations=[None, None, None],
    )

    from draftly.evaluation.online import _report_rows as online_report_rows
    from draftly.evaluation.runner import _report_rows, report_rows

    for fn in (report_rows, _report_rows, online_report_rows):
        rows = fn("documentation_only", report)
        assert len(rows) == 3, f"{fn.__name__} must emit a row per evaluator"
        assert all(row["test_pass"] is False for row in rows)
        assert "get_agent_results" in rows[0]["reason"]
        assert [r["case"] for r in rows] == ["oauth-auth", "oauth-auth", "oauth-auth"]
        assert [r["metric"] for r in rows] == [
            "expected_contains",
            "expected_tools",
            "node:context:semantic_search",
        ]


def test_report_rows_carries_threshold() -> None:
    """Report rows expose the pass/fail threshold actually applied per case.

    ``expected_contains`` thresholds come from the case's
    ``metadata.expected_contains_threshold`` override, falling back to the
    evaluator default (0.6). Binary/tool metrics have no numeric threshold.
    """
    from strands_evals.types.evaluation_report import EvaluationReport

    report = EvaluationReport(
        overall_score=0.0,
        scores=[0.9, 0.4, 0.5],
        test_passes=[True, False, True],
        cases=[
            {
                "name": "support-q",
                "evaluator": "expected_contains",
                "metadata": {"expected_contains_threshold": 0.45},
            },
            {"name": "docs-pr", "evaluator": "expected_contains"},
            {"name": "tool-check", "evaluator": "expected_tools"},
        ],
        reasons=["", "below", ""],
    )

    from draftly.evaluation.runner import report_rows

    rows = report_rows("support", report)
    assert rows[0]["threshold"] == 0.45
    assert rows[1]["threshold"] == 0.6
    assert rows[2]["threshold"] is None
