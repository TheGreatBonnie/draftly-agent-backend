"""Runner _notify_reviewers logging tests."""

import pytest
import structlog

import draftly.workflows.runner as runner_mod
from draftly.workflows.context import WorkflowContext


class _Notifier:
    def __init__(self, result=None, error=None):
        self._result = result if result is not None else {}
        self._error = error
        self.calls = []

    async def notify_reviewers(self, run_id: str) -> dict[str, list[str]]:
        self.calls.append(run_id)
        if self._error is not None:
            raise self._error
        return self._result


def make_runner(notifier):
    return runner_mod.WorkflowRunner(WorkflowContext(notifier=notifier), publisher=None)


@pytest.mark.asyncio
async def test_notify_dispatched_logs_platform_counts(monkeypatch):
    from structlog.testing import capture_logs

    notifier = _Notifier({"slack": ["U1", "U2"], "discord": [], "email": ["e@x.com"]})

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.notify_dispatched")
        )
        await make_runner(notifier)._notify_reviewers("run-1")

    markers = [line for line in logs if line.get("event") == "review_notify_dispatched"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"
    assert markers[0]["total"] == 3
    assert markers[0]["slack_count"] == 2
    assert markers[0]["discord_count"] == 0
    assert markers[0]["email_count"] == 1


@pytest.mark.asyncio
async def test_notify_skipped_when_no_recipients(monkeypatch):
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.notify_skip_recipients")
        )
        await make_runner(_Notifier({}))._notify_reviewers("run-1")

    markers = [line for line in logs if line.get("event") == "review_notify_skipped"]
    assert [m.get("reason") for m in markers] == ["no_recipients"]


@pytest.mark.asyncio
async def test_notify_skipped_when_notifier_unavailable(monkeypatch):
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.notify_skip_unavailable")
        )
        await make_runner(None)._notify_reviewers("run-1")

    markers = [line for line in logs if line.get("event") == "review_notify_skipped"]
    assert [m.get("reason") for m in markers] == ["notifier_unavailable"]


@pytest.mark.asyncio
async def test_notify_failure_logs_dispatch_failed(monkeypatch):
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        monkeypatch.setattr(
            runner_mod, "logger", structlog.get_logger("test.notify_failure")
        )
        await make_runner(_Notifier(error=RuntimeError("boom")))._notify_reviewers("run-1")

    markers = [line for line in logs if line.get("event") == "review_notify_dispatch_failed"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"
