"""Writer draft tools contract: chunked append into the draft store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from draftly.agents.documentation.draft_scope import (
    current_draft_scope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.tools.documentation.drafts import (
    MAX_CHUNK_BYTES,
    append_chunk,
    finalize_draft,
    get_drafted_docs,
    start_draft,
)


@dataclass
class _FakeRevision:
    id: str
    sealed: bool = False
    content_size: int = 0
    path: str = ""


@dataclass
class _FakeDraftFile:
    path: str
    action: str
    content: str
    content_size: int = 0


@dataclass
class _FakeRepo:
    """Minimal DraftRepository stand-in exposing the tool surface used."""

    revisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    chunks: dict[str, list[str]] = field(default_factory=dict)
    latest: list[_FakeDraftFile] = field(default_factory=list)
    latest_calls: list[str] = field(default_factory=list)

    async def create_revision(
        self, *, run_id: str, org_id: str, generation: int, path: str, action: str
    ) -> _FakeRevision:
        draft_id = f"d-{len(self.revisions) + 1}"
        self.revisions[draft_id] = {
            "run_id": run_id,
            "org_id": org_id,
            "generation": generation,
            "path": path,
            "action": action,
            "sealed": False,
        }
        self.chunks[draft_id] = []
        return _FakeRevision(id=draft_id, path=path)

    async def append_chunk(self, draft_id: str, content: str) -> int:
        self.chunks[draft_id].append(content)
        return len(self.chunks[draft_id])

    async def finalize(self, draft_id: str) -> _FakeRevision:
        if draft_id not in self.revisions:
            raise ValueError(f"unknown draft_id {draft_id!r}")
        self.revisions[draft_id]["sealed"] = True
        return _FakeRevision(
            id=draft_id,
            sealed=True,
            content_size=sum(len(c) for c in self.chunks[draft_id]),
            path=self.revisions[draft_id]["path"],
        )

    async def get_latest(self, *, run_id: str) -> list[_FakeDraftFile]:
        self.latest_calls.append(run_id)
        return list(self.latest)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _FakeRepo:
    fake = _FakeRepo()

    def _build(*args: Any, **kwargs: Any) -> _FakeRepo:
        return fake

    monkeypatch.setattr("draftly.tools.documentation.drafts.build_draft_repository", _build)
    return fake


@pytest.fixture
def scoped_run() -> None:
    class _Scope:
        run_id = "run-1"
        org_id = "org-1"
        generation = 1

    token = set_draft_scope(_Scope())
    try:
        yield
    finally:
        reset_draft_scope(token)


async def test_start_draft_requires_active_scope() -> None:
    assert current_draft_scope() is None
    with pytest.raises(ValueError, match="scope"):
        await start_draft(repository="acme/api", path="docs/a.md", action="update")


async def test_start_draft_returns_draft_id(repo: _FakeRepo, scoped_run: None) -> None:
    result = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    assert "draft_id" in result
    assert result["draft_id"].startswith("d-")
    assert repo.revisions[result["draft_id"]]["path"] == "docs/a.md"


async def test_start_draft_rejects_unsafe_paths(repo: _FakeRepo, scoped_run: None) -> None:
    for bad in ("../escape.md", "/etc/passwd", "a/../../b.md", "docs/..", "docs/a\0.md"):
        with pytest.raises(ValueError, match="path"):
            await start_draft(repository="acme/api", path=bad, action="update")


async def test_start_draft_rejects_empty_path(repo: _FakeRepo, scoped_run: None) -> None:
    with pytest.raises(ValueError, match="path"):
        await start_draft(repository="acme/api", path="", action="update")


async def test_append_chunk_rejects_oversized(repo: _FakeRepo, scoped_run: None) -> None:
    started = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    with pytest.raises(ValueError, match="MAX_CHUNK_BYTES|24_000|chunk"):
        await append_chunk(started["draft_id"], "x" * (MAX_CHUNK_BYTES + 1))


async def test_append_chunk_rejects_empty(repo: _FakeRepo, scoped_run: None) -> None:
    started = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    with pytest.raises(ValueError, match="empty"):
        await append_chunk(started["draft_id"], "")


async def test_append_chunk_rejects_chunk_size_mismatch(
    repo: _FakeRepo, scoped_run: None
) -> None:
    started = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    with pytest.raises(ValueError, match="chunk_size"):
        await append_chunk(started["draft_id"], "hello", chunk_size=4)


async def test_append_chunk_accepts_valid_and_reports_count(
    repo: _FakeRepo, scoped_run: None
) -> None:
    started = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    first = await append_chunk(started["draft_id"], "hello", chunk_size=5)
    second = await append_chunk(started["draft_id"], " world")
    assert first == {"received_chunks": 1, "sealed": False}
    assert second == {"received_chunks": 2, "sealed": False}


async def test_finalize_returns_seal(repo: _FakeRepo, scoped_run: None) -> None:
    started = await start_draft(repository="acme/api", path="docs/a.md", action="update")
    await append_chunk(started["draft_id"], "hello world")
    result = await finalize_draft(started["draft_id"])
    assert result["sealed"] is True
    assert result["path"] == "docs/a.md"
    assert result["size_bytes"] == 11


async def test_finalize_unknown_draft(repo: _FakeRepo, scoped_run: None) -> None:
    with pytest.raises(ValueError, match="unknown"):
        await finalize_draft("nope")


async def test_get_drafted_docs_requires_active_scope() -> None:
    assert current_draft_scope() is None
    with pytest.raises(ValueError, match="scope"):
        await get_drafted_docs()


async def test_get_drafted_docs_returns_sealed_bodies(repo: _FakeRepo, scoped_run: None) -> None:
    repo.latest = [
        _FakeDraftFile(
            path="docs/auth.md",
            action="update",
            content="# Auth\n- PKCE",
            content_size=14,
        ),
        _FakeDraftFile(
            path="docs/rbac.md",
            action="create",
            content="# RBAC\n- organization scoping",
            content_size=33,
        ),
    ]

    result = await get_drafted_docs()

    assert result["files"] == [
        {
            "path": "docs/auth.md",
            "action": "update",
            "content": "# Auth\n- PKCE",
            "content_available": True,
        },
        {
            "path": "docs/rbac.md",
            "action": "create",
            "content": "# RBAC\n- organization scoping",
            "content_available": True,
        },
    ]
    assert repo.latest_calls == ["run-1"]


async def test_get_drafted_docs_reads_only_same_run(repo: _FakeRepo, scoped_run: None) -> None:
    repo.latest = []
    result = await get_drafted_docs()
    assert result == {"files": []}
    assert repo.latest_calls == ["run-1"]
