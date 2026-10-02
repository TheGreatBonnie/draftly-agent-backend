"""Modal deployment wrapper for the short-lived Draftly RQ worker."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import modal

APP_NAME = "draftly-rq-worker"
SECRET_NAME = "draftly-worker"
CRON_EXPRESSION = "* * * * *"
CPU_CORES = 0.125
MEMORY_MB = 512
TIMEOUT_SECONDS = 21_000
MAX_CONTAINERS = 1
MAX_CONCURRENT_INPUTS = 2
SCALEDOWN_WINDOW_SECONDS = 2

_DRAIN_LOCK = threading.Lock()

RUNTIME_ENV = {
    "ENVIRONMENT": "production",
    "LOG_LEVEL": "INFO",
    "RQ_ENABLED": "true",
    "RQ_BURST": "1",
    "STRANDS_SESSION_STORAGE": "database",
}

# Modal imports this module from /root/rq_worker.py, not from its repo path, so
# parents[2] exists only in the local checkout and raised IndexError on every
# container start. Image.from_dockerfile() reads the Dockerfile lazily inside a
# build closure, so the remote import never opens it and only needs a value that
# does not raise. /app is the real remote repo root: Dockerfile.modal sets
# WORKDIR /app and copies src/ and workers/ there.
_MODULE_PARENTS = Path(__file__).resolve().parents
REPO_ROOT = _MODULE_PARENTS[2] if len(_MODULE_PARENTS) > 2 else Path("/app")

# Absolute path to the uv venv built by docker/Dockerfile.modal. Resolving
# `python` through PATH would make this depend on whatever Modal prepends when
# it post-processes the image, so a Python without the project dependencies
# could win and the worker would fail at import time instead of build time.
VENV_PYTHON = "/app/.venv/bin/python"

app = modal.App(APP_NAME)
image = modal.Image.from_dockerfile(
    REPO_ROOT / "docker/Dockerfile.modal",
    context_dir=REPO_ROOT,
)
worker_secret = modal.Secret.from_name(SECRET_NAME)


def run_worker_process() -> None:
    """Run the existing worker entrypoint as the image's unprivileged user."""
    subprocess.run(
        [VENV_PYTHON, "-m", "workers.rq_worker"],
        cwd="/app",
        check=True,
        user="draftly",
        group="render-secrets",
    )


def run_worker_if_idle(lock: threading.Lock = _DRAIN_LOCK) -> bool:
    """Run one drain, or skip when another scheduled input is still active."""
    if not lock.acquire(blocking=False):
        return False
    try:
        run_worker_process()
        return True
    finally:
        lock.release()


@app.function(
    image=image,
    secrets=[worker_secret],
    env=RUNTIME_ENV,
    schedule=modal.Cron(CRON_EXPRESSION),
    cpu=CPU_CORES,
    memory=MEMORY_MB,
    timeout=TIMEOUT_SECONDS,
    max_containers=MAX_CONTAINERS,
    scaledown_window=SCALEDOWN_WINDOW_SECONDS,
)
@modal.concurrent(max_inputs=MAX_CONCURRENT_INPUTS)
def drain_rq() -> None:
    """Drain every configured RQ queue and exit when no work remains."""
    run_worker_if_idle()


@app.local_entrypoint()
def main() -> None:
    """Invoke one remote drain for smoke tests and demos."""
    drain_rq.remote()
