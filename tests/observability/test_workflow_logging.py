from __future__ import annotations

import structlog


def test_workflow_context_is_available_to_handlers_and_cleared_afterward() -> None:
    from draftly.observability.workflow_logging import bind_workflow_context

    seen: dict[str, object] = {}
    with bind_workflow_context(
        "github_release.enqueue",
        {
            "run_id": "run-7",
            "event": {
                "event_id": "event-7",
                "event_type": "release.published",
                "repository": "acme/api",
                "project_id": "org-1",
            },
        },
    ):
        seen.update(structlog.contextvars.get_contextvars())

    assert seen == {
        "task_name": "github_release.enqueue",
        "run_id": "run-7",
        "event_id": "event-7",
        "event_type": "release.published",
        "repository": "acme/api",
        "org_id": "org-1",
    }
    assert structlog.contextvars.get_contextvars() == {}
