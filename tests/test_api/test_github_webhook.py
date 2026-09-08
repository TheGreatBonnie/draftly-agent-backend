from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _pr_event(event_type: str) -> dict:
    return {"event_type": event_type, "repository": "acme/api", "event_id": "ev-5"}


async def test_non_merged_pr_not_enqueued() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(return_value=_pr_event("pull_request.edited"))
    # full mock setup for new dispatch path (even though skip leaves no job)
    jobs = MagicMock()
    jobs.upsert_on_conflict = AsyncMock()
    context = MagicMock()
    context.repositories = MagicMock(jobs=jobs)
    workflows = MagicMock(context=context)
    worker = MagicMock()
    worker.run_task = AsyncMock()
    request.app.state.draftly = MagicMock(
        events=events,
        workflows=workflows,
        worker=worker,
        rq_queues={"webhooks": MagicMock()},
        task_handlers={"github_pr.enqueue": MagicMock()},
        settings=MagicMock(rq_enabled=False),
    )

    body = b"{}"
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "X-Hub-Signature-256": "sha256=0000",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-1",
    }

    bt = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(
            routes_mod, "verify_webhook_signature", lambda body, sig: True
        )
        result = await github_webhook(request=request, background_tasks=bt)

    assert "skipped" in str(result)
    jobs.upsert_on_conflict.assert_not_awaited()
    bt.add_task.assert_not_called()


async def test_merged_pr_enqueued() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(return_value=_pr_event("pull_request.merged"))
    jobs = MagicMock()
    jobs.upsert_on_conflict = AsyncMock()
    context = MagicMock()
    context.repositories = MagicMock(jobs=jobs)
    workflows = MagicMock(context=context)
    worker = MagicMock()
    worker.run_task = AsyncMock()
    request.app.state.draftly = MagicMock(
        events=events,
        workflows=workflows,
        worker=worker,
        rq_queues={"webhooks": MagicMock()},
        task_handlers={"github_pr.enqueue": MagicMock()},
        settings=MagicMock(rq_enabled=False),
    )
    request.body = AsyncMock(return_value=b"{}")
    request.headers = {
        "X-Hub-Signature-256": "sha256=0000",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-2",
    }

    bt = MagicMock()
    identity = AsyncMock(return_value=("org-1", 42))
    with (
        patch("draftly.app.api.routes.github._resolve_webhook_identity", new=identity),
        patch(
            "draftly.persistence.repositories.github.save_github_workflow",
            new=AsyncMock(return_value="wf-1"),
        ),
        pytest.MonkeyPatch.context() as mp,
    ):
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        result = await github_webhook(request=request, background_tasks=bt)

    assert result.status.startswith("pull_request.merged")
    drafts = request.app.state.draftly
    assert events.normalize_github.return_value["project_id"] == "org-1"
    bt.add_task.assert_called_once_with(
        drafts.worker.run_task,
        "github_pr.enqueue",
        event=events.normalize_github.return_value,
        run_id="ev-5",
    )


async def test_opened_pr_enqueued() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(return_value=_pr_event("pull_request.opened"))
    jobs = MagicMock()
    jobs.upsert_on_conflict = AsyncMock()
    context = MagicMock()
    context.repositories = MagicMock(jobs=jobs)
    workflows = MagicMock(context=context)
    worker = MagicMock()
    worker.run_task = AsyncMock()
    request.app.state.draftly = MagicMock(
        events=events,
        workflows=workflows,
        worker=worker,
        rq_queues={"webhooks": MagicMock()},
        task_handlers={"github_pr.enqueue": MagicMock()},
        settings=MagicMock(rq_enabled=False),
    )
    request.body = AsyncMock(return_value=b"{}")
    request.headers = {
        "X-Hub-Signature-256": "sha256=0000",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-2",
    }

    bt = MagicMock()
    identity = AsyncMock(return_value=("org-1", 42))
    with (
        patch("draftly.app.api.routes.github._resolve_webhook_identity", new=identity),
        patch(
            "draftly.persistence.repositories.github.save_github_workflow",
            new=AsyncMock(return_value="wf-1"),
        ),
        pytest.MonkeyPatch.context() as mp,
    ):
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        result = await github_webhook(request=request, background_tasks=bt)

    assert result.status.startswith("pull_request.opened")
    bt.add_task.assert_called_once_with(
        request.app.state.draftly.worker.run_task,
        "github_pr.enqueue",
        event=events.normalize_github.return_value,
        run_id="ev-5",
    )


async def test_push_and_release_not_blocked() -> None:
    from draftly.app.api.routes.github import github_webhook

    for event_type in ("push.pushed", "release.published"):
        request = MagicMock()
        # push/release events also need event_id
        event_data = {"event_type": event_type, "repository": "acme/api", "event_id": "ev-5"}
        events = MagicMock()
        events.normalize_github = AsyncMock(return_value=event_data)
        jobs = MagicMock()
        jobs.upsert_on_conflict = AsyncMock()
        context = MagicMock()
        context.repositories = MagicMock(jobs=jobs)
        workflows = MagicMock(context=context)
        worker = MagicMock()
        worker.run_task = AsyncMock()
        request.app.state.draftly = MagicMock(
            events=events,
            workflows=workflows,
            worker=worker,
            rq_queues={"webhooks": MagicMock()},
            task_handlers={"github_pr.enqueue": MagicMock()},
            settings=MagicMock(rq_enabled=False),
        )
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {
            "X-Hub-Signature-256": "sha256=0000",
            "X-GitHub-Event": event_type.split(".")[0],
            "X-GitHub-Delivery": "d-3",
        }

        bt = MagicMock()
        identity = AsyncMock(return_value=("org-1", 42))
        with (
            patch("draftly.app.api.routes.github._resolve_webhook_identity", new=identity),
            patch(
                "draftly.persistence.repositories.github.save_github_workflow",
                new=AsyncMock(return_value="wf-1"),
            ),
            pytest.MonkeyPatch.context() as mp,
        ):
            import draftly.app.api.routes.github as routes_mod

            mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
            await github_webhook(request=request, background_tasks=bt)

        expected_task = (
            "github_release.enqueue"
            if event_type.startswith("release")
            else "github_pr.enqueue"
        )
        assert bt.add_task.call_args.args[1] == expected_task, event_type
