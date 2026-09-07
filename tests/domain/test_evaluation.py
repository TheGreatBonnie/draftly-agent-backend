"""§8.9 verification: evaluation harness (§8.2, §8.10)."""

from __future__ import annotations

from typing import Any, cast

from strands_evals import Case
from strands_evals.types import EvaluationData

from draftly.evaluation import FailureAnalyzer, StrandsEvalsRunner
from draftly.evaluation.evaluators import (
    Contains,
    DocumentationQualityEvaluator,
    build_documentation_quality_evaluator,
)
from draftly.evaluation.store import DatabaseEvaluationDataStore


class FakeEvaluationRepository:
    def __init__(self):
        self.records: list[dict] = []

    async def create(self, **kwargs):
        self.records.append(kwargs)
        return {"id": f"eval-{len(self.records)}", **kwargs}


def identity_response(case: Case) -> str:
    """Deterministic task: echo the expected output (always passes)."""
    return case.expected_output or ""


class TestStrandsEvalsRunner:
    def test_loads_golden_datasets(self) -> None:
        runner = StrandsEvalsRunner()
        datasets = runner.load_all_datasets()
        assert {"documentation", "support", "github_issues"} <= set(datasets)
        for cases in datasets.values():
            assert all(isinstance(c, Case) for c in cases)

    def test_loads_canonical_content_dataset_cases(self) -> None:
        cases = StrandsEvalsRunner.load_dataset("content")

        assert [case.name for case in cases] == [
            "grounded_release_variants",
            "unsupported_variant_is_blocked",
        ]

    def test_content_dataset_is_in_default_suite(self) -> None:
        definitions = StrandsEvalsRunner().load_all_dataset_definitions()

        assert "content" in {definition["name"] for definition in definitions}

    async def test_deterministic_evaluator_run_passes(self) -> None:
        runner = StrandsEvalsRunner()
        cases = [
            Case(name="c1", input="q1", expected_output="retries use backoff"),
            Case(name="c2", input="q2", expected_output="see retries guide"),
        ]
        report = await runner.run(
            cases,
            evaluators=[Contains(value="retries")],
            get_response=identity_response,
        )
        assert float(report.overall_score) == 1.0
        assert all(report.test_passes)

    async def test_failure_detected_and_persisted(self) -> None:
        repo = FakeEvaluationRepository()
        runner = StrandsEvalsRunner(repository=repo)
        cases = [
            Case(
                name="will-fail",
                input="q",
                expected_output="the answer mentions zebra unicorns",
            )
        ]
        report, record = await runner.run_and_persist(
            cases,
            evaluators=[Contains(value="zebra unicorns")],
            get_response=lambda c: "a completely wrong answer",
        )
        assert not all(report.test_passes)
        assert record is not None
        assert record["status"] == "failed"
        assert record["failures"][0]["case"] == "will-fail"

    async def test_documentation_quality_evaluator_matches_gate(self) -> None:
        from strands_evals.types import EvaluationData

        evaluator = DocumentationQualityEvaluator()
        data = EvaluationData(
            name="quality",
            input="write docs",
            actual_output="See docs/transactions for retry details",
            metadata={"evidence": [{"id": "docs/transactions", "topic": "retry"}]},
        )
        outputs = evaluator.evaluate(data)
        assert len(outputs) == 1
        assert outputs[0].test_pass is True
        assert outputs[0].score > 0.5

    def test_documentation_quality_factory_matches_live_signature(self) -> None:
        # Regression: build_live_evaluators imports
        # build_documentation_quality_evaluator(model=judge_model). This would
        # have thrown ImportError and aborted every live run.
        evaluator = build_documentation_quality_evaluator(model=object())
        assert isinstance(evaluator, DocumentationQualityEvaluator)
        assert evaluator.name == "documentation_quality"


class TestFailureAnalyzer:
    def test_categorizes_by_reason_keywords(self) -> None:
        analyzer = FailureAnalyzer()
        analysis = analyzer.analyze(
            [
                {"case": "a", "reason": "answer missing key steps"},
                {"case": "b", "reason": "unsupported claim without citation"},
                {"case": "c", "reason": "wrong formula used"},
                {"case": "d", "reason": "something indescribable"},
            ]
        )
        assert analysis.total_failures == 4
        assert analysis.categories["completeness"] == 1
        assert analysis.categories["grounding"] == 1
        assert analysis.categories["correctness"] == 1
        assert analysis.categories["other"] == 1
        assert analysis.dominant_category() in analysis.categories


class TestEvaluationDataStore:
    def test_protocol_cache_roundtrip(self) -> None:
        store = DatabaseEvaluationDataStore()
        data = Case(name="c1", input="x")
        store.save("c1", cast(EvaluationData[Any, Any], data))
        assert store.load("c1") is data
        assert store.load("missing") is None
