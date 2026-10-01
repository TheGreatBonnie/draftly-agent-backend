"""GitHub Actions workflows must run Python from the project venv.

Regression: `uv sync --frozen --no-dev` creates `.venv` but does NOT add
`.venv/bin` to PATH. A bare `python ...` therefore resolves to the
GitHub-hosted runner's system interpreter, which has none of the project's
dependencies installed:

    Run python scripts/bootstrap.py
    ModuleNotFoundError: No module named 'dotenv'

`python-dotenv` IS a declared runtime dependency (pyproject.toml), and it IS
present in `.venv` -- only the wrong interpreter is being used. Note this does
not affect Render, where the Dockerfile sets `ENV PATH="/app/.venv/bin:..."`.

Both workflows were affected: `migrate.yml` and `rq-worker.yml`.

The fix is to invoke Python through `uv run`, which resolves and activates the
project environment for the command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO_ROOT / ".github/workflows").glob("*.yml"))

#: Steps that execute Python or the module entrypoint.
_PYTHON_STEP = re.compile(r"\b(python3?|uvicorn)\b")


def _steps() -> list[tuple[str, str, str]]:
    """Yield (filename, job_id, run_command) for every `run:` step."""
    out: list[tuple[str, str, str]] = []
    for path in WORKFLOWS:
        doc = yaml.safe_load(path.read_text())
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                cmd = step.get("run")
                if cmd:
                    out.append((path.name, job_id, cmd))
    return out


PYTHON_STEPS = [(f, j, c) for f, j, c in _steps() if _PYTHON_STEP.search(c)]


def test_at_least_one_python_step_exists():
    """Guards against the parametrised set silently becoming empty."""
    assert PYTHON_STEPS, "expected the workflows to invoke Python somewhere"


@pytest.mark.parametrize("filename,job,command", PYTHON_STEPS, ids=lambda v: str(v)[:60])
def test_python_runs_through_uv_or_the_venv(filename: str, job: str, command: str):
    """A bare `python` cannot see project dependencies.

    `uv sync` does not activate the environment, so the runner's system
    interpreter is used and every third-party import fails.
    """
    bare = re.search(r"(?<![\w/-])python3?\s", command)
    if not bare:
        return  # no Python invocation in this step

    assert "uv run" in command, (
        f"{filename}/{job}: invokes bare `python` after `uv sync`, which leaves "
        f"PATH untouched. The runner's system Python has no project "
        f"dependencies and fails with ModuleNotFoundError. Use `uv run python "
        f"...`. Command: {command!r}"
    )


@pytest.mark.parametrize("filename,job,command", PYTHON_STEPS, ids=lambda v: str(v)[:60])
def test_python_steps_are_not_bare_after_uv_sync(filename: str, job: str, command: str):
    """No unqualified `python`/`python3` anywhere in a run step."""
    for line in command.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"(?<![\w/-])python3?\s", stripped):
            assert "uv run" in stripped, (
                f"{filename}/{job}: bare interpreter on line {stripped!r}. "
                f"Prefix with `uv run`."
            )


def test_migrate_workflow_uses_uv_run():
    """The migrate workflow is the one failing in CI."""
    text = (REPO_ROOT / ".github/workflows/migrate.yml").read_text()
    assert "uv run python scripts/bootstrap.py" in text


def test_worker_workflow_uses_uv_run():
    """The worker executes queued jobs and needs the same fix."""
    text = (REPO_ROOT / ".github/workflows/rq-worker.yml").read_text()
    assert "uv run python -m workers.rq_worker" in text, (
        "rq-worker.yml must invoke the worker through `uv run` or it fails "
        "with the same ModuleNotFoundError as migrate.yml"
    )


def test_github_app_private_key_is_mapped_from_secrets():
    """The PEM must be mapped into env explicitly.

    A bare `$GITHUB_APP_PRIVATE_KEY` in a `run:` block is NOT populated from
    repository secrets -- only `${{ secrets.X }}` interpolation is. Without the
    mapping the workflow silently writes an empty .pem and every GitHub App
    operation fails later with a confusing error.
    """
    text = (REPO_ROOT / ".github/workflows/rq-worker.yml").read_text()
    assert "$GITHUB_APP_PRIVATE_KEY" in text, "expected the PEM to be materialized"
    assert "GITHUB_APP_PRIVATE_KEY: ${{ secrets.GITHUB_APP_PRIVATE_KEY }}" in text, (
        "GITHUB_APP_PRIVATE_KEY must be mapped from secrets into the step env"
    )


def test_requesty_key_is_not_required_by_the_worker():
    """Requesty is optional; the allowlist fallback covers the stage models."""
    text = (REPO_ROOT / ".github/workflows/rq-worker.yml").read_text()
    assert "REQUESTY_API_KEY" not in text, (
        "rq-worker.yml should not require REQUESTY_API_KEY; resolve_model falls "
        "back by capability when Requesty is disabled"
    )


def test_every_referenced_secret_is_declared_in_the_docs():
    """Each secrets.* reference must be documented in the deployment guide."""
    guide = (REPO_ROOT / "docs/deployment/render.md").read_text()
    for path in WORKFLOWS:
        for name in set(re.findall(r"secrets\.([A-Z_0-9]+)", path.read_text())):
            assert name in guide, f"{name} (used by {path.name}) is undocumented"
