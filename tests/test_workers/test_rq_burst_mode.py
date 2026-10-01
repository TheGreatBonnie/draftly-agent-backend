"""The worker must exit once the queue drains when RQ_BURST=1.

GitHub Actions runs the worker as a short-lived job: it drains whatever is
queued and returns instead of blocking forever on BLPOP. `work(burst=True)`
is the RQ primitive that does this. It is gated behind an env var so local
`python -m workers.rq_worker` keeps its normal long-running behaviour.

`resolve_burst_mode` is imported inside each test rather than at module
scope because importing `workers.rq_worker` pulls in
`draftly.app.lifecycle` and the settings model, which reads DATABASE_URL and
REDIS_URL. Deferring the import keeps this unit test free of that.
"""

from __future__ import annotations

import pytest


def test_burst_disabled_by_default():
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({}) is False


@pytest.mark.parametrize(
    "value",
    ["", "0", "true", "True", "yes", "on", "2", " 1"],
)
def test_burst_requires_exact_string_one(value: str):
    """Only the exact string "1" enables burst mode.

    Stricter than a truthy parse on purpose: a typo must not silently
    turn a 6-hour CI job into an indefinite blocking worker, and must not
    silently disable burst either.
    """
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({"RQ_BURST": value}) is False


def test_burst_enabled_for_exact_one():
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({"RQ_BURST": "1"}) is True


def test_burst_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch):
    from workers.rq_worker import resolve_burst_mode

    monkeypatch.setenv("RQ_BURST", "1")
    assert resolve_burst_mode() is True

    monkeypatch.delenv("RQ_BURST", raising=False)
    assert resolve_burst_mode() is False


def test_unexpected_worker_loop_failure_is_propagated() -> None:
    """Modal must see a failed invocation when RQ's work loop crashes."""
    from unittest.mock import Mock

    from workers.rq_worker import run_worker_loop

    worker = Mock()
    worker.work.side_effect = RuntimeError("redis connection lost")

    with pytest.raises(RuntimeError, match="redis connection lost"):
        run_worker_loop(worker, burst=True, log=Mock())
