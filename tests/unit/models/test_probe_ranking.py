"""Tests for the probe tool's pure ranking logic."""

from __future__ import annotations

from tests.scripts.probe_models import ModelResult, ProbeResult, rank_results


def _result(
    name: str,
    passed: int,
    total: int,
    latency: float,
) -> ModelResult:
    result = ModelResult(name, "prov", 10, ())
    result.probes = [
        ProbeResult(f"p{i}", "ok" if i < passed else "FAIL") for i in range(total)
    ]
    result.probes[0].latency_s = latency
    return result


class TestRankResults:
    def test_most_probes_passed_ranks_first(self) -> None:
        ranked = rank_results(
            [_result("weak", 0, 3, 1.0), _result("strong", 3, 3, 9.0)]
        )

        assert [r.model_name for r in ranked] == ["strong", "weak"]

    def test_equal_passes_rank_by_latency(self) -> None:
        ranked = rank_results(
            [_result("slow", 2, 2, 5.0), _result("quick", 2, 2, 1.2)]
        )

        assert [r.model_name for r in ranked] == ["quick", "slow"]

    def test_zero_probe_model_sorts_last_among_equals(self) -> None:
        ranked = rank_results(
            [_result("dead", 0, 1, 0.0), _result("alive", 1, 1, 30.0)]
        )

        assert [r.model_name for r in ranked] == ["alive", "dead"]

    def test_stable_for_ties(self) -> None:
        a = _result("a", 1, 1, 2.0)
        b = _result("b", 1, 1, 2.0)

        assert rank_results([a, b]) == [a, b]
