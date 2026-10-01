"""Deployment contract for the scheduled Modal RQ worker."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from infra.modal import rq_worker as modal_worker


def test_modal_worker_has_bounded_single_container_runtime() -> None:
    """A bad resource/schedule edit must not create an always-on cost leak."""
    assert modal_worker.CRON_EXPRESSION == "* * * * *"
    assert modal_worker.CPU_CORES == 0.125
    assert modal_worker.MEMORY_MB == 512
    assert modal_worker.TIMEOUT_SECONDS == 21_000
    assert modal_worker.MAX_CONTAINERS == 1
    assert modal_worker.SCALEDOWN_WINDOW_SECONDS == 2


def test_modal_worker_forces_drain_and_exit_mode() -> None:
    """The scheduled process must exit when Redis queues become empty."""
    assert modal_worker.RUNTIME_ENV == {
        "ENVIRONMENT": "production",
        "LOG_LEVEL": "INFO",
        "RQ_ENABLED": "true",
        "RQ_BURST": "1",
        "STRANDS_SESSION_STORAGE": "database",
    }


def test_worker_process_uses_the_container_user_and_entrypoint(monkeypatch) -> None:
    """Modal ignores Docker USER, so the child must explicitly drop privileges."""
    recorded: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded["command"] = command
        recorded.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    runner: Callable[..., subprocess.CompletedProcess[str]] = fake_run
    monkeypatch.setattr(modal_worker.subprocess, "run", runner)

    modal_worker.run_worker_process()

    assert recorded == {
        "command": ["python", "-m", "workers.rq_worker"],
        "cwd": "/app",
        "check": True,
        "user": "draftly",
        "group": "render-secrets",
    }
