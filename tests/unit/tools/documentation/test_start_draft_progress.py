"""``start_draft`` must report existing progress when re-opened.

Run d76e2490 issued 5 ``start_draft`` calls for 3 pages: ``faq.md`` called it
twice, 14 seconds apart, for the same ``draft_id ae36cc97``. ``create_revision``
is already idempotent, so the second call returned the existing revision rather
than duplicating a row — but the tool returned a bare ``{"draft_id": ...}``,
which gives the model no way to tell a fresh open from a re-open, so it
re-derived state it already had.

Also pins the guard that a page-scoped writer must carry an assigned repository:
a scope with an ``assigned_page_id`` but no ``repository`` is a wiring bug, and
silently accepting it lets a writer draft against whatever repository the model
invents.
"""

from __future__ import annotations

import pytest

from draftly.agents.documentation.draft_scope import (
    DraftProgress,
    DraftScope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.tools.documentation import drafts


class _Revision:
    def __init__(self, draft_id: str, *, sealed: bool = False, chunks: int = 0) -> None:
        self.id = draft_id
        self.sealed = sealed
        self.content_size = chunks


def _scope(**overrides) -> DraftScope:
    base = {
        "run_id": "run-1",
        "org_id": "org-1",
        "generation": 1,
        "version": 1,
        "assigned_page_id": "docs/explanation/faq.md",
        "repository": "org/repo",
        "head_sha": "abc123",
        "progress": DraftProgress(),
    }
    base.update(overrides)
    return DraftScope(**base)


def _patch_revision(monkeypatch, revision: _Revision) -> list[dict]:
    calls: list[dict] = []

    async def _create_revision(**kwargs):
        calls.append(kwargs)
        return revision

    class _FakeRepo:
        async def create_revision(self, **kwargs):
            return await _create_revision(**kwargs)

    monkeypatch.setattr(drafts, "build_draft_repository", _FakeRepo)
    return calls


@pytest.mark.asyncio
async def test_fresh_open_reports_zero_chunks(monkeypatch):
    scope = _scope()
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("ae36cc97"))
        result = await drafts.start_draft(
            repository="org/repo", path="docs/explanation/faq.md", action="update"
        )
    finally:
        reset_draft_scope(token)

    assert result["draft_id"] == "ae36cc97"
    assert result["reopened"] is False
    assert result["chunks"] == 0
    assert result["sealed"] is False
    assert "append_chunk" in result["message"]


@pytest.mark.asyncio
async def test_reopen_reports_existing_progress(monkeypatch):
    """The re-open case from run d76e2490."""
    progress = DraftProgress(draft_id="ae36cc97", chunks=3)
    scope = _scope(progress=progress)
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("ae36cc97", chunks=3))
        result = await drafts.start_draft(
            repository="org/repo", path="docs/explanation/faq.md", action="update"
        )
    finally:
        reset_draft_scope(token)

    assert result["draft_id"] == "ae36cc97"
    assert result["reopened"] is True
    assert result["chunks"] == 3
    assert "already open" in result["message"]
    # The point of the fix: tell the model not to re-plan what it has.
    assert "already" in result["message"].lower()


@pytest.mark.asyncio
async def test_reopen_does_not_reset_the_chunk_counter(monkeypatch):
    """A re-open must not zero ``progress.chunks``.

    ``start_draft`` assigns ``progress.draft_id`` unconditionally. If the model
    re-opens a draft that already has content, resetting ``chunks`` to 0 would
    erase the evidence that the MaxTokens resume path reads to build its
    continuation prompt.
    """
    progress = DraftProgress(draft_id="ae36cc97", chunks=7)
    scope = _scope(progress=progress)
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("ae36cc97", chunks=7))
        await drafts.start_draft(
            repository="org/repo", path="docs/explanation/faq.md", action="update"
        )
    finally:
        reset_draft_scope(token)

    assert progress.chunks == 7


@pytest.mark.asyncio
async def test_reopen_of_sealed_draft_reports_sealed(monkeypatch):
    progress = DraftProgress(draft_id="ae36cc97", chunks=9, sealed=True)
    scope = _scope(progress=progress)
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("ae36cc97", sealed=True, chunks=9))
        result = await drafts.start_draft(
            repository="org/repo", path="docs/explanation/faq.md", action="update"
        )
    finally:
        reset_draft_scope(token)

    assert result["sealed"] is True
    assert result["reopened"] is True
    # A sealed draft must not be told to append more content.
    assert "append_chunk" not in result["message"]


@pytest.mark.asyncio
async def test_reopen_with_a_different_draft_id_is_a_fresh_open(monkeypatch):
    """Only a *matching* draft id counts as a re-open.

    A new ``artifact_version`` yields a new ``draft_id`` even on the same page;
    reporting that as a re-open would tell the model it has content it does not.
    """
    progress = DraftProgress(draft_id="stale-from-last-version", chunks=4)
    scope = _scope(progress=progress)
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("ae36cc97", chunks=0))
        result = await drafts.start_draft(
            repository="org/repo", path="docs/explanation/faq.md", action="update"
        )
    finally:
        reset_draft_scope(token)

    assert result["reopened"] is False
    assert result["chunks"] == 0


@pytest.mark.asyncio
async def test_page_scoped_scope_requires_an_assigned_repository(monkeypatch):
    """A page-scoped writer with no assigned repository is a wiring bug.

    Pinning existing intended behaviour: the writer is scoped to one page but
    has no repository to draft against, so accepting the call lets the model
    invent one.
    """
    scope = _scope(repository=None)
    token = set_draft_scope(scope)
    try:
        _patch_revision(monkeypatch, _Revision("d-1"))
        with pytest.raises(ValueError, match="no assigned repository"):
            await drafts.start_draft(
                repository="org/repo",
                path="docs/explanation/faq.md",
                action="update",
            )
    finally:
        reset_draft_scope(token)
