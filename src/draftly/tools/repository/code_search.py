"""Code search tool over a local repository checkout."""

from __future__ import annotations

import os
from pathlib import Path

from strands.tools import tool

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


@tool
async def code_search(
    query: str,
    repo_dir: str,
    limit: int = 20,
    case_sensitive: bool = False,
) -> list[dict]:
    """Search source files in the local repository checkout for a query string."""
    needle = query if case_sensitive else query.lower()
    matches = []
    root = Path(repo_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIPPED_DIRS
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                lines = path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
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
