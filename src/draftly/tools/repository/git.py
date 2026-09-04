"""Git tools for inspecting a local repository checkout."""

from __future__ import annotations

import asyncio
import subprocess
import time

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

# Bound git calls so a hung/locked git process fails loudly instead of eating
# the graph's whole node_timeout budget (and never blocking the event loop).
GIT_TIMEOUT_SECONDS = 30.0


def _run_git(repo_dir: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


async def _run_git_async(repo_dir: str, *args: str) -> str:
    start = time.perf_counter()
    try:
        out = await asyncio.wait_for(
            asyncio.to_thread(_run_git, repo_dir, *args),
            timeout=GIT_TIMEOUT_SECONDS,
        )
        logger.debug(
            "git_call_done",
            repo_dir=repo_dir,
            args=list(args),
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        return out
    except TimeoutError:
        logger.error(
            "git_call_timeout",
            repo_dir=repo_dir,
            args=list(args),
            timeout_seconds=GIT_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"git call exceeded {GIT_TIMEOUT_SECONDS}s (hung/locked)",
        )
        raise
    except Exception:
        logger.exception("git_call_error", repo_dir=repo_dir, args=list(args))
        raise


@tool
async def git_status(repo_dir: str) -> str:
    """Return the porcelain git status of the local repository checkout."""
    require_nonempty(repo_dir, "repo_dir", "git_status")
    return await _run_git_async(repo_dir, "status", "--porcelain")


@tool
async def git_diff(
    repo_dir: str,
    base: str | None = None,
    head: str = "HEAD",
) -> str:
    """Return the unified diff of the local repository checkout."""
    require_nonempty(repo_dir, "repo_dir", "git_diff")
    if base and head != "HEAD":
        return await _run_git_async(repo_dir, "diff", f"{base}...{head}")
    return await _run_git_async(repo_dir, "diff", base or "HEAD")


@tool
async def git_log(repo_dir: str, limit: int = 20) -> list[dict]:
    """Return recent commit metadata from the local repository checkout."""
    require_nonempty(repo_dir, "repo_dir", "git_log")
    output = await _run_git_async(
        repo_dir,
        "log",
        "--pretty=format:%H|%an|%ad|%s",
        "--date=iso-strict",
        "-n",
        str(limit),
    )
    commits = []
    for line in output.splitlines():
        if not line:
            continue
        sha, author, date, subject = line.split("|", 3)
        commits.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return commits
