from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from draftly.app.composition.workflows import ComposedWorkflows


def test_pr_enqueue_task_in_registry() -> None:
    from draftly.app.composition.workers import TASK_REGISTRY

    assert "github_pr.enqueue" in TASK_REGISTRY
    assert TASK_REGISTRY["github_pr.enqueue"] == "github_pr"


def test_release_enqueue_task_in_registry() -> None:
    from draftly.app.composition.workers import TASK_REGISTRY

    assert TASK_REGISTRY["github_release.enqueue"] == "github_release"


def test_pr_enqueue_task_buildable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_task_runner must register a handler for github_pr.enqueue.

    build_task_runner iterates the FULL TASK_REGISTRY and raises ValueError if
    any workflow name is missing from the registry, so we patch TASK_REGISTRY
    down to just the github_pr.enqueue entry to keep the test hermetic.
    """

    async def run_pull_request_workflow(context, event, run_id=None):
        return None

    import draftly.app.composition.workers as workers_mod

    monkeypatch.setattr(
        workers_mod,
        "TASK_REGISTRY",
        {"github_pr.enqueue": "github_pr"},
    )

    workflows = ComposedWorkflows(
        registry={"github_pr": run_pull_request_workflow},
        context=MagicMock(),
    )
    dependencies = MagicMock()

    from draftly.app.composition.workers import build_task_runner

    runner = build_task_runner(workflows=workflows, dependencies=dependencies)
    assert runner.has_task("github_pr.enqueue")


def test_pr_enqueue_task_routes_to_webhooks_queue() -> None:
    from draftly.app.composition.rq_jobs import get_queue_for_task

    assert get_queue_for_task("github_pr.enqueue") == "webhooks"


def test_review_resume_task_in_registry() -> None:
    from draftly.app.composition.workers import TASK_REGISTRY

    assert TASK_REGISTRY["review.resume"] == "review_resume"


def test_review_resume_task_buildable(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_task_runner must register a handler for review.resume.

    build_task_runner iterates the FULL TASK_REGISTRY and raises ValueError if
    any workflow name is missing from the registry, so we patch TASK_REGISTRY
    down to just the review.resume entry to keep the test hermetic.
    """

    async def run_review_resume(context, **kwargs):
        return None

    import draftly.app.composition.workers as workers_mod

    monkeypatch.setattr(
        workers_mod,
        "TASK_REGISTRY",
        {"review.resume": "review_resume"},
    )

    workflows = ComposedWorkflows(
        registry={"review_resume": run_review_resume},
        context=MagicMock(),
    )
    dependencies = MagicMock()

    from draftly.app.composition.workers import build_task_runner

    runner = build_task_runner(workflows=workflows, dependencies=dependencies)
    assert runner.has_task("review.resume")


def test_review_resume_task_routes_to_webhooks_queue() -> None:
    from draftly.app.composition.rq_jobs import get_queue_for_task

    assert get_queue_for_task("review.resume") == "webhooks"
