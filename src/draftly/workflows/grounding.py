"""Run-grounding resolution for the documentation workflow.

A pull_request run has three evidence sources:

- ``local``: a real repository checkout is mounted (``repo_dir``) — use the
  local repository tools (git_*, read_file, ...) and never the GitHub API.
- ``github``: no checkout, but a GitHub installation is linked — use the
  read-only GitHub API tools to gather PR/diff/file evidence.
- ``docs``: neither — documentation-store search only.

The runner resolves grounding from the event before building the graph and
injects it (mode + repo_dir) into the graph factory and invocation state so
every node reasons over evidence that actually exists.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

LOCAL = "local"
GITHUB = "github"
DOCS = "docs"

DEFAULT_CHECKOUT_ROOT = "/tmp/repos"
_CHECKOUT_ROOT_ENV = "DRAFTLY_REPO_CHECKOUT_ROOT"

_grounding: ContextVar[dict[str, Any]] = ContextVar("run_grounding", default={})


def set_grounding(grounding: dict[str, Any]) -> Token[dict[str, Any]]:
    """Bind the run's grounding for the current workflow execution."""
    return _grounding.set(dict(grounding))


def reset_grounding(token: Token[dict[str, Any]]) -> None:
    """Restore the previous run-grounding context."""
    _grounding.reset(token)


def current_grounding() -> dict[str, Any]:
    """Return the current run's grounding (empty when unset)."""
    return _grounding.get()


def resolve_grounding(*, repo_dir: str | None, installation_id: int | None) -> str:
    """Pick the evidence mode for a run.

    A real checkout always wins over a linked installation: when the repo is
    local we must not burn the node budget on GitHub API round-trips.
    """
    if repo_dir:
        logger.info(
            "grounding_mode",
            mode=LOCAL,
            repo_dir=repo_dir,
            installation_id=installation_id,
        )
        return LOCAL
    if installation_id:
        logger.info(
            "grounding_mode",
            mode=GITHUB,
            repo_dir=None,
            installation_id=installation_id,
        )
        return GITHUB
    logger.info("grounding_mode", mode=DOCS, repo_dir=None, installation_id=None)
    return DOCS


def _is_git_checkout(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


def _checkout_root() -> Path:
    return Path(os.environ.get(_CHECKOUT_ROOT_ENV, DEFAULT_CHECKOUT_ROOT))


def repo_checkout_for(
    event: dict[str, Any],
    checkout_root: str | None = None,
) -> str | None:
    """Resolve the concrete local checkout backing this event, if any.

    A ``repo_dir`` in the event is trusted when the directory exists; otherwise
    the checked-out root (``/tmp/repos/<owner>/<repo>``) is probed for a git
    worktree so agents are never sent to a hallucinated path.
    """
    explicit = event.get("repo_dir")
    if explicit:
        path = Path(str(explicit))
        return str(path) if path.is_dir() else None

    repository = str(event.get("repository") or "").strip("/")
    owner, _, name = repository.partition("/")
    if not (owner and name):
        return None
    candidate = _checkout_root() if checkout_root is None else Path(checkout_root)
    candidate = candidate / owner / name
    return str(candidate) if _is_git_checkout(candidate) else None
