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

    request = _fabricate_request("pull_request.merged")
    bt = MagicMock()

    with patch("draftly.app.api.routes.github._tickets") as mock_tickets, \
         pytest.MonkeyPatch.context() as mp:
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        # _tickets(request) -> store whose .issue(...) is async
        mock_tickets.return_value.issue = AsyncMock(return_value="ticket-abc")

        result = await github_webhook(request=request, background_tasks=bt)

    drafts = request.app.state.draftly
    # jobs row created (fatal path)
    drafts.workflows.context.repositories.jobs.upsert_on_conflict.assert_awaited_once()
    # ticket issued
    mock_tickets.return_value.issue.assert_awaited_once_with(run_id="ev-1", org_id="")
    # in-process fallback scheduled on BackgroundTasks (rq_enabled=False)
    bt.add_task.assert_called_once_with(
        drafts.worker.run_task,
        "github_pr.enqueue",
        event=drafts.events.normalize_github.return_value,
        run_id="ev-1",
    )
    assert result.status.startswith("pull_request.merged")


async def test_merged_pr_dispatches_via_rq_when_enabled() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = _fabricate_request("pull_request.merged")
    request.app.state.draftly.settings.rq_enabled = True
    bt = MagicMock()

    with patch("draftly.app.api.routes.github._tickets") as mock_tickets, \
         patch("draftly.app.api.routes.github.enqueue_job") as mock_enqueue, \
         pytest.MonkeyPatch.context() as mp:
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        mock_tickets.return_value.issue = AsyncMock(return_value="ticket-abc")
        mock_enqueue.return_value.id = "rq-1"

        await github_webhook(request=request, background_tasks=bt)

    mock_enqueue.assert_called_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["task_name"] == "github_pr.enqueue"
    assert kwargs["run_id"] == "ev-1"
    # in-process worker NOT scheduled in RQ mode
    bt.add_task.assert_not_called()
