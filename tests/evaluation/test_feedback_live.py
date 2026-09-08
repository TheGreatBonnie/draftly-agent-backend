"""Feedback surface live-task output contract.

The feedback online task short-circuits the Strands client and runs the
deterministic feedback graph (summarize -> detect_gaps -> prioritize ->
enqueue). Its result nodes never match the ``answer``/``deliver`` node names
that ``extract_output_text`` scans, so the task must render its own
human-readable gap report into ``result["output"]``. Every output-reading
metric (expected_contains, expected_gap_detected, the LLM judges) scores that
text; when it is empty all of them fail.
"""

from __future__ import annotations

from typing import Any

from strands_evals import Case

from draftly.evaluation.online import build_online_task


class _NoopClient:
    async def invoke(self, **kwargs: Any) -> Any:
        raise AssertionError("feedback surface must not invoke the client")


def _feedback_case(name: str, input: str, threshold: int) -> Case:
    return Case(
        name=name,
        input=input,
        expected_output="",
        metadata={"surface": "feedback", "gap_threshold": threshold},
    )


async def _run(case: Case) -> dict:
    task = build_online_task(_NoopClient())
    return await task(case)


def _feedback_state(result: dict) -> Any:
    return next(e for e in result["environment_state"] if e.name == "feedback")


async def test_feedback_task_renders_detected_gap_report() -> None:
    result = await _run(
        _feedback_case(
            "three-same-topic",
            '[{"topic":"oauth","question":"How do I set up OAuth?"},'
            '{"topic":"oauth","question":"OAuth redirect fails with state mismatch"},'
            '{"topic":"oauth","question":"OAuth token expired after 1 hour"}]',
            threshold=2,
        )
    )
    output = result["output"].lower()
    assert output, "feedback task must render a gap report, not empty output"
    assert "gap" in output
    assert "detected" in output
    assert "oauth" in output
    assert "count" in output
    state = _feedback_state(result)
    assert state.state["gap_count"] == 1
    assert state.state["prioritized_gaps"][0]["topic"] == "oauth"


async def test_feedback_task_renders_no_gaps_report() -> None:
    result = await _run(
        _feedback_case(
            "scattered-unrelated",
            '[{"topic":"oauth","question":"How do I set up OAuth?"},'
            '{"topic":"rbac","question":"How do roles work?"},'
            '{"topic":"tokens","question":"What are refresh tokens?"},'
            '{"topic":"webhooks","question":"How do webhooks fire?"}]',
            threshold=3,
        )
    )
    output = result["output"].lower()
    assert output, "feedback task must render a no-gaps report, not empty output"
    assert "no gaps detected" in output
    for topic in ("oauth", "rbac", "tokens", "webhooks"):
        assert topic in output
    state = _feedback_state(result)
    assert state.state["gap_count"] == 0
    assert state.state["prioritized_gaps"] == []


async def test_feedback_env_state_exposes_grounding_evidence() -> None:
    """The feedback env_state must carry the deterministic clusters + threshold
    so the rendered claims (per-topic counts, threshold) are traceable to
    structured evidence — the groundedness judge refuses numeric claims it
    cannot verify in ActualEnvironmentState."""
    result = await _run(
        _feedback_case(
            "scattered-unrelated",
            '[{"topic":"oauth","question":"How do I set up OAuth?"},'
            '{"topic":"rbac","question":"How do roles work?"},'
            '{"topic":"tokens","question":"What are refresh tokens?"},'
            '{"topic":"webhooks","question":"How do webhooks fire?"}]',
            threshold=3,
        )
    )
    state = _feedback_state(result).state
    assert state["threshold"] == 3
    assert state["gap_count"] == 0
    clusters = {c["topic"]: c["count"] for c in state["clusters"]}
    assert clusters == {"oauth": 1, "rbac": 1, "tokens": 1, "webhooks": 1}


async def test_feedback_task_renders_gaps_in_priority_order() -> None:
    result = await _run(
        _feedback_case(
            "mixed-severities",
            '[{"topic":"auth-breaking","question":"Login completely broken after update"},'
            '{"topic":"auth-breaking","question":"Cannot authenticate at all"},'
            '{"topic":"how-to-pkce","question":"How do I enable PKCE?"},'
            '{"topic":"how-to-pkce","question":"PKCE setup guide?"}]',
            threshold=2,
        )
    )
    output = result["output"].lower()
    assert output
    assert "auth-breaking" in output
    assert "how-to-pkce" in output
    assert "count" in output
    assert (
        output.index("auth-breaking") < output.index("how-to-pkce")
    ), "higher-frequency gap must be reported first (priority order)"