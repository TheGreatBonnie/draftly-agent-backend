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

    async def run_evaluations_async(self, task):
        self.task_calls.append(task)
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
