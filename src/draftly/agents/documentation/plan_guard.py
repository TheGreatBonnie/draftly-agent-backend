"""DocChangePlan hardening (fix for truncated tool-input JSON).

The writer previously emitted full markdown files inline in one JSON tool
call. When the payload exceeded the streaming parser budget it was cut
mid-string, producing `failed to parse tool input json, defaulting to
empty dict` and an empty plan downstream.

Guards:
- `MAX_FILE_CONTENT_CHARS` / `MAX_FILES_PER_PLAN`: keep each plan small;
  split larger edits across multiple plans.
- `parse_plan_json_strict`: raise `ValueError` on truncated/invalid JSON
  instead of silently returning `{}`.
- `validate_plan_dict`: reject empty `files` with a clear message.
- `chunk_content`: split oversized content on safe boundaries.
"""

from __future__ import annotations

import json
from typing import Any

MAX_FILE_CONTENT_CHARS = 12_000
MAX_FILES_PER_PLAN = 2
MAX_TOTAL_CONTENT_CHARS = 16_000


def chunk_content(content: str, limit: int = MAX_FILE_CONTENT_CHARS) -> list[str]:
    """Split content into <=limit chunks, preferring newline boundaries."""
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + limit, len(content))
        if end < len(content):
            boundary = content.rfind("\n", start, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(content[start:end])
        start = end
    return chunks


def validate_plan_dict(plan: dict[str, Any]) -> dict[str, Any]:
    """Reject empty/invalid plans with an actionable error (no silent {})."""
    files = plan.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError(
            "DocChangePlan requires at least one file "
            "(files: [{path, content, action: create|update}]); "
            "got empty/missing files — accumulate edits into 1-2 files "
            f"(<= {MAX_FILES_PER_PLAN} files, <= {MAX_TOTAL_CONTENT_CHARS} total chars)"
        )
    if len(files) > MAX_FILES_PER_PLAN:
        raise ValueError(
            f"DocChangePlan has {len(files)} files, max is {MAX_FILES_PER_PLAN}; "
            "consolidate the edits into fewer, larger documents"
        )
    total = 0
    for entry in files:
        content = entry.get("content", "") if isinstance(entry, dict) else ""
        total += len(content)
        if len(content) > MAX_FILE_CONTENT_CHARS:
            raise ValueError(
                f"File {entry.get('path', '?')!r} content is {len(content)} chars "
                f"(max {MAX_FILE_CONTENT_CHARS}); split with chunk_content()"
            )
    if total > MAX_TOTAL_CONTENT_CHARS:
        raise ValueError(
            f"DocChangePlan total content is {total} chars "
            f"(max {MAX_TOTAL_CONTENT_CHARS}); trim the plan so it streams cleanly"
        )
    return plan


def parse_plan_json_strict(raw: str) -> dict[str, Any]:
    """Parse a DocChangePlan JSON string; raise on truncated/invalid input."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"truncated or invalid DocChangePlan JSON ({exc}); "
            "emit smaller plans: one file per call, escape newlines as \\n"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("DocChangePlan JSON must decode to an object")
    return validate_plan_dict(parsed)
