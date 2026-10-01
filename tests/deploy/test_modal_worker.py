"""Deployment contract for the scheduled Modal RQ worker."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from infra.modal import rq_worker as modal_worker

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/deploy-modal-worker.yml"


def _deployment_workflow() -> dict[str, Any]:
    assert WORKFLOW_PATH.exists(), "Modal deployment workflow is missing"
    return yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)


def test_modal_worker_has_bounded_single_container_runtime() -> None:
    """A bad resource/schedule edit must not create an always-on cost leak."""
    assert modal_worker.CRON_EXPRESSION == "* * * * *"
    assert modal_worker.CPU_CORES == 0.125
    assert modal_worker.MEMORY_MB == 512
    assert modal_worker.TIMEOUT_SECONDS == 21_000
    assert modal_worker.MAX_CONTAINERS == 1
    assert modal_worker.MAX_CONCURRENT_INPUTS == 2
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


def test_overlapping_schedule_input_skips_a_duplicate_drain(monkeypatch) -> None:
    """A cron tick arriving during a long job must not queue another drain."""
    active_drain = threading.Lock()
    active_drain.acquire()
    monkeypatch.setattr(
        modal_worker,
        "run_worker_process",
        lambda: pytest.fail("duplicate drain must not start"),
    )

    assert modal_worker.run_worker_if_idle(active_drain) is False


def test_modal_deployment_tracks_the_modal_branch() -> None:
    """A push to the deployment branch must publish the matching worker code."""
    workflow = _deployment_workflow()

    assert workflow["on"]["push"]["branches"] == ["modal-deployment"]
    assert workflow["on"]["push"]["paths"] == [
        "infra/modal/**",
        "docker/Dockerfile.render",
        ".dockerignore",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "scripts/bootstrap.py",
        "src/**",
        "workers/**",
        ".github/workflows/deploy-modal-worker.yml",
    ]


def test_modal_deployment_uses_only_modal_credentials() -> None:
    """Application credentials belong in Modal Secret, not GitHub Actions."""
    workflow = _deployment_workflow()
    env = workflow["jobs"]["deploy"]["env"]

    assert env == {
        "MODAL_TOKEN_ID": "${{ secrets.MODAL_TOKEN_ID }}",
        "MODAL_TOKEN_SECRET": "${{ secrets.MODAL_TOKEN_SECRET }}",
        "MODAL_ENVIRONMENT": "main",
    }


def test_modal_deployment_uses_locked_dependencies_and_rolling_strategy() -> None:
    """CI must deploy reproducibly without interrupting an active drain."""
    workflow = _deployment_workflow()
    steps = workflow["jobs"]["deploy"]["steps"]
    uses = [step.get("uses") for step in steps]
    commands = [step.get("run") for step in steps]

    assert "actions/checkout@v4" in uses
    assert "astral-sh/setup-uv@v5" in uses
    assert "uv sync --frozen --extra dev" in commands
    assert (
        "uv run modal deploy --strategy rolling infra/modal/rq_worker.py"
        in commands
    )


def test_legacy_github_worker_is_removed() -> None:
    """Modal must be the only deployed queue consumer."""
    legacy_workflow = REPO_ROOT / ".github/workflows/rq-worker.yml"

    assert not legacy_workflow.exists()


def test_render_blueprints_do_not_deploy_a_worker() -> None:
    """Modal must be the only hosted RQ worker on this branch."""
    for filename in ("render.yaml", "render.free.yaml"):
        blueprint = yaml.safe_load((REPO_ROOT / filename).read_text())

        assert all(service["type"] != "worker" for service in blueprint["services"])


def test_render_queues_are_reachable_from_modal() -> None:
    """Each Render queue must expose its TLS endpoint to the external worker."""
    for filename in ("render.yaml", "render.free.yaml"):
        blueprint = yaml.safe_load((REPO_ROOT / filename).read_text())
        queues = [
            service
            for service in blueprint["services"]
            if service["type"] == "keyvalue"
        ]

        assert queues
        assert all(
            {rule["source"] for rule in queue["ipAllowList"]} == {"0.0.0.0/0"}
            for queue in queues
        )


def test_render_api_tracks_the_modal_deployment_branch() -> None:
    """The supporting API must deploy the same application revision."""
    for filename in ("render.yaml", "render.free.yaml"):
        blueprint = yaml.safe_load((REPO_ROOT / filename).read_text())
        web_services = [
            service for service in blueprint["services"] if service["type"] == "web"
        ]

        assert web_services
        assert all(
            service["branch"] == "modal-deployment" for service in web_services
        )
