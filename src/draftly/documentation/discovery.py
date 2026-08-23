"""Deterministic glob-based classification of file paths into documentation vs other."""

from __future__ import annotations

from fnmatch import fnmatch


def discover_documentation(
    paths: list[str],
    include: list[str],
    exclude: list[str],
) -> list[str]:
    """Filter file paths to documentation files based on include/exclude globs."""
    result: list[str] = []

    for path in paths:
        if _matches_any(path, exclude):
            continue
        if _matches_any(path, include):
            result.append(path)

    return sorted(result)


def _matches_any(path: str, patterns: list[str]) -> bool:
    """Check if a path matches any of the given glob patterns."""
    for pattern in patterns:
        if fnmatch(path, pattern):
            return True
        # Handle directory globs: docs/** matches docs/anything
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if path.startswith(prefix + "/") or path == prefix:
                return True
    return False
