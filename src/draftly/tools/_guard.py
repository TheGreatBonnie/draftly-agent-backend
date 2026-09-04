"""Shared validation guards for draftly tools.

A Strands tool-input parse failure in the event loop swaps the intended JSON
arguments for an empty dict (``{}``), which silently zeroes every required
string parameter. That made tools walk the wrong directory, return garbage,
and cause the model to thrash them in a loop until the node timed out. These
guards turn the ``{}`` case into a clear, retryable tool error instead of a
silent no-op with a broken argument.
"""

from __future__ import annotations


class EmptyToolInputError(RuntimeError):
    """Raised when a tool received no usable input (Strands parse-drop to {})."""


class OversizedToolInputError(RuntimeError):
    """Raised when a tool argument exceeds its size budget (truncation risk)."""


class AbsolutePathError(RuntimeError):
    """Raised when a repo tool gets an absolute path instead of repo-relative."""


#: Max excerpt chars per evidence item (pointers, not file dumps).
MAX_EVIDENCE_EXCERPT_CHARS = 2000
#: Max chars for a repo-relative path argument.
MAX_PATH_ARG_CHARS = 500


def require_nonempty(value: str, field: str, tool_name: str) -> None:
    """Raise if a required string argument is empty/blank (parse-drop signature)."""
    if not value or not value.strip():
        raise EmptyToolInputError(
            f"{tool_name}: required argument '{field}' is empty. "
            "The tool call was received without its JSON arguments (a model "
            "streaming / tool-input parse issue). Re-issue this tool call with "
            "a real value for '{field}'."
        )


def require_max_length(value: str, limit: int, field: str, tool_name: str) -> None:
    """Raise if a string argument exceeds its budget (truncation risk).

    Oversized args get cut mid-string by the streaming parser, producing
    `failed to parse tool input json, defaulting to empty dict`. Fail loudly
    with an excerpt instruction so the model retries small in one step.
    """
    if len(value) > limit:
        raise OversizedToolInputError(
            f"{tool_name}: argument '{field}' is {len(value)} chars "
            f"(max {limit}). Emit an excerpt, don't dump full file content; "
            "read one file per call and summarize."
        )


def require_repo_relative_path(value: str, field: str, tool_name: str) -> None:
    """Raise if a repo path is absolute (wastes arg budget, breaks scoping)."""
    if value.strip().startswith("/"):
        raise AbsolutePathError(
            f"{tool_name}: argument '{field}' must be repo-relative, "
            f"got absolute path {value[:80]!r}... Pass a repo-relative path "
            "plus repo_dir instead."
        )
    require_max_length(value, MAX_PATH_ARG_CHARS, field, tool_name)


def validate_evidence_item(item: dict) -> dict:
    """Reject evidence items carrying full file dumps (truncation source)."""
    content = item.get("content", "") if isinstance(item, dict) else ""
    if isinstance(content, str) and len(content) > MAX_EVIDENCE_EXCERPT_CHARS:
        raise OversizedToolInputError(
            f"EvidenceBundle item for {item.get('path', '?')!r} carries "
            f"{len(content)} chars (max {MAX_EVIDENCE_EXCERPT_CHARS}). "
            "Store an excerpt with source ids, not the full file."
        )
    return item
