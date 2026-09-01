from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _pr_event(event_type: str) -> dict:
    return {"event_type": event_type, "repository": "acme/api"}


async def test_non_merged_pr_not_enqueued() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(return_value=_pr_event("pull_request.opened"))
    runner = MagicMock()
    runner.run = AsyncMock()
    workflows = MagicMock()
    workflows.runner = runner
    request.app.state.draftly = MagicMock(events=events, workflows=workflows)

    # Bypass signature/body parsing: monkeypatch the header + parse path is
    # heavy, so drive the gate via the normalize step. Use the standalone
    # route body contract: pass a fabricated request whose body/headers are
    # satisfied by signing (see Step 4 for the full HTTP-level path).
    body = b"{}"
    request.body = AsyncMock(return_value=body)
    request.headers = {
        "X-Hub-Signature-256": "sha256=0000",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-1",
    }

    # Patch signature verification to accept (avoids needing the real secret).
    with pytest.MonkeyPatch.context() as mp:
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(
            routes_mod, "verify_webhook_signature", lambda body, sig: True
        )
        result = await github_webhook(request=request, background_tasks=MagicMock())

    assert "skipped" in str(result)
    runner.run.assert_not_awaited()


async def test_merged_pr_enqueued() -> None:
    from draftly.app.api.routes.github import github_webhook

    request = MagicMock()
    events = MagicMock()
    events.normalize_github = AsyncMock(return_value=_pr_event("pull_request.merged"))
    runner = MagicMock()
    runner.run = AsyncMock()
    workflows = MagicMock()
    workflows.runner = runner
    request.app.state.draftly = MagicMock(events=events, workflows=workflows)
    request.body = AsyncMock(return_value=b"{}")
    request.headers = {
        "X-Hub-Signature-256": "sha256=0000",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-2",
    }

    bt = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        import draftly.app.api.routes.github as routes_mod

        mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
        result = await github_webhook(request=request, background_tasks=bt)

    assert result["accepted"] is True
    bt.add_task.assert_called_once()


async def test_push_and_release_not_blocked() -> None:
    from draftly.app.api.routes.github import github_webhook

    for event_type in ("push.pushed", "release.published"):
        request = MagicMock()
        events = MagicMock()
        events.normalize_github = AsyncMock(return_value=_pr_event(event_type))
        runner = MagicMock()
        runner.run = AsyncMock()
        workflows = MagicMock()
        workflows.runner = runner
        request.app.state.draftly = MagicMock(events=events, workflows=workflows)
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {
            "X-Hub-Signature-256": "sha256=0000",
            "X-GitHub-Event": event_type.split(".")[0],
            "X-GitHub-Delivery": "d-3",
        }

        bt = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            import draftly.app.api.routes.github as routes_mod

            mp.setattr(routes_mod, "verify_webhook_signature", lambda body, sig: True)
            await github_webhook(request=request, background_tasks=bt)

        bt.add_task.assert_called_once(), event_type
