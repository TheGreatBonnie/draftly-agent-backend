"""Code search tool over a local repository checkout."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

# The underlying walk is synchronous and runs across the whole checkout; bound
# it so a giant/hung worktree fails loudly instead of eating the graph's whole
# node_timeout budget (and never blocking the async event loop meanwhile).
SEARCH_TIMEOUT_SECONDS = 30.0

_SKIPPED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
}


def _walk(repo_dir: str, query: str, limit: int, case_sensitive: bool) -> list[dict]:
    needle = query if case_sensitive else query.lower()
    matches = []
    root = Path(repo_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIPPED_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    matches.append(
                        {
                            "path": str(path.relative_to(root)),
                            "line": line_number,
                            "content": line.strip()[:200],
                        }
                    )
                    if len(matches) >= limit:
                        return matches
    return matches


@tool
async def code_search(
    query: str,
    repo_dir: str,
    limit: int = 20,
    case_sensitive: bool = False,
) -> list[dict]:
    """Search source files in the local repository checkout for a query string."""
    require_nonempty(query, "query", "code_search")
    require_nonempty(repo_dir, "repo_dir", "code_search")
    start = time.perf_counter()
    try:
        matches = await asyncio.wait_for(
            asyncio.to_thread(_walk, repo_dir, query, limit, case_sensitive),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        logger.debug(
            "code_search_done",
            repo_dir=repo_dir,
            query=query,
            limit=limit,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            matches=len(matches),
        )
        return matches
    except TimeoutError:
        logger.error(
            "code_search_timeout",
            repo_dir=repo_dir,
            query=query,
            timeout_seconds=SEARCH_TIMEOUT_SECONDS,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error=f"code search exceeded {SEARCH_TIMEOUT_SECONDS}s (over-large or hung worktree)",
        )
        raise
    except Exception:
        logger.exception(
            "code_search_error",
            repo_dir=repo_dir,
            query=query,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
