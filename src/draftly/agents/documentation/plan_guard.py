"""DocChangePlan hardening (fix for truncated tool-input JSON).

The writer emitted full markdown files inline in one JSON tool call. When the
payload exceeded the streaming parser budget it was cut mid-string, producing
`failed to parse tool input json, defaulting to empty dict` and an empty plan
downstream. The draft store (``draftly/tools/documentation/drafts.py``) is the
fix: file bytes stream through ``append_chunk`` and never appear in the plan.

Plan guards:
- `parse_plan_json_strict`: raise `ValueError` on truncated/invalid JSON
  instead of silently returning `{}`.
- `validate_plan_dict`: reject empty `files` with a clear message and require
  each entry to name a path (metadata-only: no content caps — content no
  longer lives in the plan at all).

The old per-file/total content caps and ``chunk_content`` are gone: sizing is
enforced at ``append_chunk`` time against ``MAX_CHUNK_BYTES`` in draft_store.
"""

from __future__ import annotations

import json
from typing import Any


def validate_plan_dict(plan: dict[str, Any]) -> dict[str, Any]:
    """Reject empty/invalid plans with an actionable error (no silent {})."""
    files = plan.get("files")
    if not isinstance(files, list) or len(files) == 0:
        raise ValueError(
            "DocChangePlan requires at least one file "
            "(files: [{path, action: create|update}]); "
            "got empty/missing files — start at least one draft with start_draft()"
        )
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError(
                "DocChangePlan file entries must be {path, action: create|update} "
                "objects with a non-empty 'path'"
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
