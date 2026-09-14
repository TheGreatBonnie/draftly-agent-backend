"""Capability-aware research planning (spec: worker-latency Task 6).

The documentation research node is built ONLY from the connectors the
documentation workflow needs: the grounded repository researcher + documentation
search for PR work. Slack/Discord are no longer research connectors and are
never planned. A missing mandatory capability fails the research node
deterministically instead of burning the LLM budget.
"""

from __future__ import annotations

from draftly.agents.documentation.research_capabilities import (
    ConnectorHealth,
    ResearchCapabilities,
    build_research_plan,
)


def test_github_pr_uses_repo_and_docs_researchers() -> None:
    plan = build_research_plan(
        grounding="github",
        capabilities=ResearchCapabilities(
            repository=True,
            documentation=True,
        ),
    )
    assert plan.researchers == ("github", "docs")
    assert plan.max_handoffs == 3


def test_support_event_never_plans_slack_or_discord_researchers() -> None:
    plan = build_research_plan(
        grounding="docs",
        capabilities=ResearchCapabilities(
            repository=False,
            documentation=True,
        ),
    )
    assert plan.researchers == ("docs",)
    assert "slack" not in plan.researchers
    assert "discord" not in plan.researchers


def test_local_pr_uses_local_repo_researcher() -> None:
    plan = build_research_plan(
        grounding="local",
        capabilities=ResearchCapabilities(
            repository=True,
            documentation=True,
        ),
    )
    assert plan.researchers == ("local", "docs")


def test_missing_repository_capability_fails_github_plan_deterministically() -> None:
    plan = build_research_plan(
        grounding="github",
        capabilities=ResearchCapabilities(
            repository=False,
            documentation=True,
        ),
    )
    assert plan.researchers == ()
    assert plan.failure is not None
    assert "repository" in plan.failure


def test_missing_documentation_capability_fails_plan() -> None:
    plan = build_research_plan(
        grounding="local",
        capabilities=ResearchCapabilities(
            repository=True,
            documentation=False,
        ),
    )
    assert plan.failure is not None
    assert "documentation" in plan.failure


def test_docs_grounding_without_repository_capability_is_fine() -> None:
    plan = build_research_plan(
        grounding="docs",
        capabilities=ResearchCapabilities(
            repository=False,
            documentation=True,
        ),
    )
    assert plan.failure is None
    assert plan.researchers == ("docs",)


def test_mark_unavailable_reports_one_diagnostic_per_run() -> None:
    health = ConnectorHealth(cooldown_seconds=300.0)
    first = health.mark_unavailable("slack", reason="authentication", run_id="run-a")
    second = health.mark_unavailable("slack", reason="authentication", run_id="run-a")
    another_run = health.mark_unavailable("slack", reason="authentication", run_id="run-b")
    assert first is True
    assert second is False
    assert another_run is True


def test_can_attempt_tracks_cooldown() -> None:
    health = ConnectorHealth(cooldown_seconds=300.0)
    assert health.can_attempt("slack")
    health.mark_unavailable("slack", reason="payment_required", run_id="run-x")
    assert not health.can_attempt("slack")
    assert health.can_attempt("discord")  # untouched connector stays available


def test_non_retryable_reasons_do_not_enter_cooldown() -> None:
    health = ConnectorHealth(cooldown_seconds=300.0)
    health.mark_unavailable("slack", reason="rate_limited", run_id="run-y")
    assert health.can_attempt("slack")


def test_capabilities_round_trip_through_serialization() -> None:
    caps = ResearchCapabilities(
        repository=True,
        documentation=False,
    )
    assert ResearchCapabilities.from_dict(caps.to_dict()) == caps


def test_capabilities_from_dict_ignores_legacy_support_fields() -> None:
    caps = ResearchCapabilities.from_dict(
        {
            "repository": True,
            "documentation": True,
            "slack_search": True,
            "discord_search": True,
        }
    )
    assert caps == ResearchCapabilities(repository=True, documentation=True)