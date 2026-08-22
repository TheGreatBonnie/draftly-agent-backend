"""Git tools for inspecting a local repository checkout."""

from __future__ import annotations

import subprocess

from strands.tools import tool


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


@tool
async def git_status(repo_dir: str) -> str:
    """Return the porcelain git status of the local repository checkout."""
    return _run_git(repo_dir, "status", "--porcelain")


@tool
async def git_diff(
    repo_dir: str,
    base: str | None = None,
    head: str = "HEAD",
) -> str:
    """Return the unified diff of the local repository checkout."""
    if base and head != "HEAD":
        return _run_git(repo_dir, "diff", f"{base}...{head}")
    return _run_git(repo_dir, "diff", base or "HEAD")


@tool
async def git_log(repo_dir: str, limit: int = 20) -> list[dict]:
    """Return recent commit metadata from the local repository checkout."""
    output = _run_git(
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
