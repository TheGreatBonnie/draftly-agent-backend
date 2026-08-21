"""Filesystem tools for working with a local repository checkout."""

from __future__ import annotations

import os
from pathlib import Path

from strands.tools import tool


@tool
async def read_file(path: str) -> str:
    """Read a file from the local repository checkout."""
    content = Path(path).read_text(encoding="utf-8")
    return content


@tool
async def write_file(path: str, content: str) -> dict:
    """Write content to a file in the local repository checkout."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size}


@tool
async def list_directory(path: str) -> list[dict]:
    """List the entries of a directory in the local repository checkout."""
    entries = []
    for entry in sorted(Path(path).iterdir(), key=lambda p: p.name.lower()):
        entries.append(
            {
                "name": entry.name,
                "path": str(entry),
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )
    return entries


@tool
async def file_exists(path: str) -> bool:
    """Return whether a file exists in the local repository checkout."""
    return os.path.isfile(path)
