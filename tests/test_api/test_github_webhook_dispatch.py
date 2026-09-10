from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fabricate_request(event_type: str, *, event_id: str = "ev-1") -> MagicMock:
    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(
        return_value={
            "event_type": event_type,
            "event_id": event_id,
            "repository": "acme/api",
        }
    )
    jobs = MagicMock()
    jobs.upsert_on_conflict = AsyncMock()
    context = MagicMock()
    context.repositories = MagicMock(jobs=jobs)
    workflows = MagicMock(context=context)

    worker = MagicMock()
    worker.run_task = AsyncMock(return_value=None)

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
        "X-GitHub-Delivery": "d-9",
    }
    return request


async def test_merged_pr_creates_job_row_and_dispatches_in_process() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = _fabricate_request("pull_request.opened")
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

    drafts = request.app.state.draftly
    # jobs row created (fatal path)
    drafts.workflows.context.repositories.jobs.upsert_on_conflict.assert_awaited_once()
    # The browser mints an org-scoped ticket after the run is persisted.
    # in-process fallback scheduled on BackgroundTasks (rq_enabled=False)
    bt.add_task.assert_called_once_with(
        drafts.worker.run_task,
        "github_pr.enqueue",
        event=drafts.events.normalize_github.return_value,
        run_id="ev-1",
    )
    assert result.status.startswith("pull_request.opened")


async def test_merged_pr_dispatches_via_rq_when_enabled() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = _fabricate_request("pull_request.opened")
    request.app.state.draftly.settings.rq_enabled = True
    bt = MagicMock()

    identity = AsyncMock(return_value=("org-1", 42))
    with (
        patch("draftly.app.api.routes.github._resolve_webhook_identity", new=identity),
        patch(
            "draftly.persistence.repositories.github.save_github_workflow",
            new=AsyncMock(return_value="wf-1"),
        ),
        patch("draftly.app.api.routes.github.enqueue_job") as mock_enqueue,
        pytest.MonkeyPatch.context() as mp,
    ):
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        mock_enqueue.return_value.id = "rq-1"

        await github_webhook(request=request, background_tasks=bt)

    mock_enqueue.assert_called_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["task_name"] == "github_pr.enqueue"
    assert kwargs["run_id"] == "ev-1"
    # in-process worker NOT scheduled in RQ mode
    bt.add_task.assert_not_called()


async def test_webhook_identity_uses_linked_github_org() -> None:
    from draftly.app.api.routes.github import _resolve_webhook_identity

    app_state = MagicMock()
    db = MagicMock()
    app_state.dependencies.integrations.database = db

    with patch(
        "draftly.persistence.repositories.github.get_org_by_github_org",
        new=AsyncMock(return_value={"clerk_org_id": "org-9"}),
    ) as lookup:
        org_id, installation_id = await _resolve_webhook_identity(
            app_state,
            {"repository": "acme/api"},
            {"installation": {"id": 77}},
        )

    assert (org_id, installation_id) == ("org-9", 77)
    lookup.assert_awaited_once_with(github_org="acme", db=db)
