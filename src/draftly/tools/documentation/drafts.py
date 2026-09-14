"""Draft store tools for the documentation writer agent.

The writer streams file bytes through small ``append_chunk`` tool calls into
``draft_chunks`` (data plane) instead of inlining full markdown in the
``DocChangePlan`` structured-output JSON (control plane). That keeps every
single tool-input payload far below the streaming parser's truncation bound —
the root cause this store exists to fix. See
``src/draftly/persistence/repositories/drafts.py``.

Every tool raises :class:`ValueError` (or the _guard errors) on invalid input;
the writer agent's judge surface turns the failure into a steering instruction
so the model retries small in one step.
"""

from __future__ import annotations

import posixpath

import structlog
from strands.tools import tool

from draftly.agents.documentation.draft_scope import DraftScope, current_draft_scope
from draftly.persistence.repositories.drafts import DraftRepository
from draftly.tools._guard import (
    AbsolutePathError,
    EmptyToolInputError,
    OversizedToolInputError,
    require_nonempty,
    require_repo_relative_path,
)

logger = structlog.get_logger(__name__)

#: Max bytes per append_chunk call. Deliberately small: keeps the tool-input
#: JSON well under the streaming parser's truncation ceiling; the model emits
#: one logical paragraph/section per call. 24_000 chars stays 4x under the
#: largest observed safe payload (the store's MAX_CHUNK_BYTES budget).
MAX_CHUNK_BYTES = 24_000


def build_draft_repository() -> DraftRepository:
    """Lazily construct the DraftRepository (tests monkeypatch this)."""
    from draftly.integrations.database.client import DatabaseClient

    return DraftRepository(database=DatabaseClient())


def _require_scope() -> DraftScope:
    scope = current_draft_scope()
    if scope is None:
        raise ValueError(
            "no active draft scope: the writer node runs outside a run context. "
            "The runner must publish a DraftScope before invoking the graph."
        )
    return scope


def _validate_rel_path(path: str, field: str, tool_name: str) -> str:
    try:
        require_nonempty(path, field, tool_name)
        require_repo_relative_path(path, field, tool_name)
    except (EmptyToolInputError, OversizedToolInputError, AbsolutePathError) as exc:
        raise ValueError(str(exc)) from exc
    if "\x00" in path:
        raise ValueError(
            f"{tool_name}: argument '{field}' contains a NUL byte. "
            "Re-issue with a normal repo-relative path."
        )
    normalized = posixpath.normpath(path)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise ValueError(
            f"{tool_name}: argument '{field}' escapes the repo "
            f"({path!r}). Keep paths repo-relative and inside the repo."
        )
    return normalized


@tool
async def start_draft(
    repository: str,
    path: str,
    action: str,
) -> dict:
    """Start a draft for one file; returns a draft_id for append_chunk.

    Arguments:
        repository: repo name the file belongs to (informational only)
        path: repo-relative path of the file being drafted
        action: 'create' for a new file, 'update' for an existing one
    """
    require_nonempty(repository, "repository", "start_draft")
    require_nonempty(action, "action", "start_draft")
    clean_path = _validate_rel_path(path, "path", "start_draft")

    scope = _require_scope()
    repo = build_draft_repository()
    revision = await repo.create_revision(
        run_id=scope.run_id,
        org_id=scope.org_id,
        generation=scope.generation,
        path=clean_path,
        action=action,
    )
    logger.info(
        "start_draft",
        draft_id=revision.id,
        run_id=scope.run_id,
        generation=scope.generation,
        path=clean_path,
        action=action,
    )
    return {"draft_id": revision.id}


@tool
async def get_drafted_docs() -> dict:
    """Read the latest sealed draft generation for this run (read-only).

    Returns the file bodies the writer node persisted to the draft store, so
    downstream delivery can commit them without the bytes ever riding in
    tool-INPUT JSON (the truncation root cause the store exists to fix).

    Returns:
        dict with ``files``: [{path, action, content, content_available}]
        — ``content_available: True`` for every sealed revision. Metadata
        paths without a sealed body are simply absent here; the reviewer
        document marks those ``content_available: False`` at persist time.
    """
    scope = _require_scope()
    repo = build_draft_repository()
    revisions = await repo.get_latest(run_id=scope.run_id)
    files = [
        {
            "path": rev.path,
            "action": rev.action,
            "content": rev.content,
            "content_available": True,
        }
        for rev in (revisions or [])
    ]
    logger.info("get_drafted_docs", run_id=scope.run_id, files=len(files))
    return {"files": files}


@tool
async def append_chunk(
    draft_id: str,
    content: str,
    chunk_size: int | None = None,
) -> dict:
    """Append a chunk of file content to a draft (max 24_000 chars).

    Arguments:
        draft_id: id returned by start_draft
        content: one logical section/paragraph of the file's content
        chunk_size: optional expected size of content (chars) for self-check

    Returns:
        dict with received_chunks count and sealed flag.
    """
    try:
        require_nonempty(draft_id, "draft_id", "append_chunk")
        require_nonempty(content, "content", "append_chunk")
    except (EmptyToolInputError, OversizedToolInputError) as exc:
        raise ValueError(str(exc)) from exc
    if len(content) > MAX_CHUNK_BYTES:
        raise ValueError(
            f"append_chunk: content is {len(content)} chars "
            f"(max MAX_CHUNK_BYTES={MAX_CHUNK_BYTES}). "
            "Emit one logical section per call, not the full file."
        )
    if chunk_size is not None and chunk_size != len(content):
        raise ValueError(
            f"append_chunk: chunk_size={chunk_size} does not match "
            f"content length {len(content)}. Re-issue with the correct value."
        )

    repo = build_draft_repository()
    count = await repo.append_chunk(draft_id, content)
    logger.debug(
        "append_chunk",
        draft_id=draft_id,
        bytes_len=len(content),
        received_chunks=count,
    )
    return {"received_chunks": count, "sealed": False}


@tool
async def finalize_draft(draft_id: str) -> dict:
    """Seal a draft once all its content has been appended.

    Arguments:
        draft_id: id returned by start_draft

    Returns:
        dict with sealed flag, path, and size_bytes.
    """
    try:
        require_nonempty(draft_id, "draft_id", "finalize_draft")
    except (EmptyToolInputError, OversizedToolInputError) as exc:
        raise ValueError(str(exc)) from exc
    repo = build_draft_repository()
    revision = await repo.finalize(draft_id)
    logger.info(
        "finalize_draft",
        draft_id=draft_id,
        size_bytes=revision.content_size,
        path=revision.path,
    )
    return {
        "sealed": bool(revision.sealed),
        "path": revision.path,
        "size_bytes": revision.content_size,
    }
