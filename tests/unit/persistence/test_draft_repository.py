"""DraftRepository contract tests for the draft store (data plane)."""

from __future__ import annotations

from typing import Any

import pytest

from draftly.persistence.repositories.drafts import (
    KEEP_GENERATIONS,
    DraftRepository,
    DraftRevision,
)


class FakeClient:
    """In-memory fake mirroring the asyncpg client surface the repo uses.

    Holds materialized ``draft_revisions``/``draft_chunks`` rows and routes
    the repository's exact SQL shape to them. ``sealed`` revisions the fake
    treats as deleted once deleted_at is set (the repo's GC deletes rows; the
    fake flags them so ``fetch_all`` keeps working).
    """

    def __init__(self) -> None:
        self.revisions: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self._id_seq = 0

    def _sid(self) -> str:
        self._id_seq += 1
        return f"draft-{self._id_seq}"

    async def execute(self, query: str, *args: Any) -> str:
        up = query.strip().upper()
        if up.startswith("INSERT INTO DRAFT_REVISIONS"):
            self.revisions.append(
                {
                    "id": args[0],
                    "run_id": args[1],
                    "org_id": args[2],
                    "generation": args[3],
                    "path": args[4],
                    "action": args[5],
                    "sealed": False,
                    "content_size": 0,
                    "created_at": "2026-09-13T00:00:00Z",
                    "sealed_at": None,
                }
            )
        elif up.startswith("INSERT INTO DRAFT_CHUNKS"):
            self.chunks.append(
                {
                    "draft_id": args[0],
                    "chunk_index": args[1],
                    "content": args[2],
                    "created_at": "2026-09-13T00:00:00Z",
                }
            )
        elif "UPDATE DRAFT_REVISIONS" in up:
            draft_id = args[0]
            for row in self.revisions:
                if row["id"] == draft_id:
                    if "SEALED = TRUE" in up.upper() and "CONTENT_SIZE" in up.upper():
                        row["sealed"] = True
                        row["content_size"] = args[1]
                    if "SEALED_AT" in up.upper() and row["sealed"]:
                        row["sealed_at"] = args[1]
        elif up.startswith("DELETE FROM DRAFT_CHUNKS"):
            draft_ids = args
            self.chunks = [
                c for c in self.chunks if c["draft_id"] not in set(draft_ids)
            ]
        elif up.startswith("DELETE FROM DRAFT_REVISIONS"):
            draft_ids = args
            for row in self.revisions:
                if row["id"] in set(draft_ids):
                    row["deleted_at"] = "2026-09-13T00:00:00Z"
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch_all(query, *args)
        return rows[0] if rows else None

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.strip()
        if q.upper().startswith("SELECT MAX(GENERATION)"):
            rows = [r for r in self.revisions if r["run_id"] == args[0] and not r.get("deleted_at")]
            return [{"generation": max(r["generation"] for r in rows)}] if rows else None

        if "FROM DRAFT_CHUNKS" in q.upper():
            draft_id = args[0]
            rows = [c for c in self.chunks if c["draft_id"] == draft_id]
            if "ORDER BY" in q.upper():
                rows.sort(key=lambda c: c["chunk_index"])
            return rows

        if "FROM DRAFT_REVISIONS" in q.upper():
            rows = [r for r in self.revisions if not r.get("deleted_at")]
            if "ORDER BY GENERATION DESC" in q.upper():
                rows = sorted(rows, key=lambda r: (r["generation"], r["path"]), reverse=True)
                return rows
            if "WHERE RUN_ID = $1 AND GENERATION = $2" in q.upper():
                rows = [r for r in rows if r["run_id"] == args[0] and r["generation"] == args[1]]
            elif "WHERE RUN_ID = $1" in q.upper():
                rows = [r for r in rows if r["run_id"] == args[0]]
            elif "WHERE ID = $1" in q.upper():
                rows = [r for r in rows if r["id"] == args[0]]
            return rows

        return []


@pytest.fixture
def repo() -> DraftRepository:
    return DraftRepository(database=FakeClient())


async def test_create_revision_returns_unsealed_record(repo: DraftRepository) -> None:
    revision = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/guide.md", action="update"
    )
    assert isinstance(revision, DraftRevision)
    assert revision.sealed is False
    assert revision.content_size == 0
    assert revision.path == "docs/guide.md"


async def test_append_chunk_orders_and_counts(repo: DraftRepository) -> None:
    revision = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/guide.md", action="update"
    )
    first = await repo.append_chunk(revision.id, "hello ")
    second = await repo.append_chunk(revision.id, "world")
    assert first == 1
    assert second == 2


async def test_finalize_marks_sealed_and_sizes(repo: DraftRepository) -> None:
    revision = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/guide.md", action="update"
    )
    await repo.append_chunk(revision.id, "hello ")
    await repo.append_chunk(revision.id, "world")
    sealed = await repo.finalize(revision.id)
    assert sealed.sealed is True
    assert sealed.content_size == 11
    assert sealed.sealed_at is not None


async def test_append_after_finalize_raises(repo: DraftRepository) -> None:
    revision = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/guide.md", action="update"
    )
    await repo.finalize(revision.id)
    with pytest.raises(ValueError, match="sealed"):
        await repo.append_chunk(revision.id, "more")


async def test_append_unknown_draft_raises(repo: DraftRepository) -> None:
    with pytest.raises(ValueError, match="unknown"):
        await repo.append_chunk("does-not-exist", "x")


async def test_get_generation_only_returns_sealed_with_assembled_content(
    repo: DraftRepository,
) -> None:
    rev1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev1.id, "AAA")
    await repo.finalize(rev1.id)
    rev2 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/b.md", action="create"
    )
    await repo.append_chunk(rev2.id, "BBB")

    files = await repo.get_generation(run_id="run-1", generation=1)
    assert [f.path for f in files] == ["docs/a.md"]
    assert files[0].content == "AAA"


async def test_assembly_joins_chunks_in_order(repo: DraftRepository) -> None:
    rev = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev.id, "one ")
    await repo.append_chunk(rev.id, "two ")
    await repo.append_chunk(rev.id, "three")
    await repo.finalize(rev.id)
    files = await repo.get_generation(run_id="run-1", generation=1)
    assert files[0].content == "one two three"


async def test_get_latest_keeps_latest_sealed_per_path_across_generations(
    repo: DraftRepository,
) -> None:
    rev1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev1.id, "old")
    await repo.finalize(rev1.id)
    rev2 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=2, path="docs/b.md", action="create"
    )
    await repo.append_chunk(rev2.id, "new")
    await repo.finalize(rev2.id)

    by_path = {f.path: f.content for f in await repo.get_latest(run_id="run-1")}
    assert by_path == {"docs/a.md": "old", "docs/b.md": "new"}


async def test_get_latest_partial_supersession_keeps_siblings(
    repo: DraftRepository,
) -> None:
    a1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(a1.id, "old-a")
    await repo.finalize(a1.id)
    b1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/b.md", action="create"
    )
    await repo.append_chunk(b1.id, "beta")
    await repo.finalize(b1.id)
    a2 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=2, path="docs/a.md", action="update"
    )
    await repo.append_chunk(a2.id, "new-a")
    await repo.finalize(a2.id)

    by_path = {f.path: f.content for f in await repo.get_latest(run_id="run-1")}
    assert by_path == {"docs/a.md": "new-a", "docs/b.md": "beta"}


async def test_get_path_latest_returns_newest_sealed_for_path(
    repo: DraftRepository,
) -> None:
    rev1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev1.id, "old-a")
    await repo.finalize(rev1.id)
    rev2 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=2, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev2.id, "new-a")
    await repo.finalize(rev2.id)

    latest = await repo.get_path_latest(run_id="run-1", path="docs/a.md")
    assert latest is not None
    assert latest.content == "new-a"


async def test_get_path_latest_none_when_unsealed_or_missing(
    repo: DraftRepository,
) -> None:
    rev = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev.id, "drafting")
    assert await repo.get_path_latest(run_id="run-1", path="docs/a.md") is None
    assert await repo.get_path_latest(run_id="run-1", path="docs/none.md") is None


async def test_get_latest_empty_when_no_sealed(repo: DraftRepository) -> None:
    rev = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.append_chunk(rev.id, "drafting")
    assert await repo.get_latest(run_id="run-1") == []


async def test_next_generation_starts_at_one_then_increments(repo: DraftRepository) -> None:
    assert await repo.next_generation(run_id="run-1") == 1
    await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    assert await repo.next_generation(run_id="run-1") == 2


async def test_list_revisions(repo: DraftRepository) -> None:
    rev1 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=1, path="docs/a.md", action="update"
    )
    await repo.finalize(rev1.id)
    rev2 = await repo.create_revision(
        run_id="run-1", org_id="org-1", generation=2, path="docs/b.md", action="create"
    )
    rows = await repo.list_revisions(run_id="run-1")
    assert len(rows) == 2
    assert rev2.id in {r.id for r in rows}
    gen1 = await repo.list_revisions(run_id="run-1", generation=1)
    assert [r.id for r in gen1] == [rev1.id]
    assert gen1[0].sealed is True


async def test_gc_keeps_latest_generations_and_never_unsealed(repo: DraftRepository) -> None:
    gc_seen = []
    for gen in range(1, KEEP_GENERATIONS + 1):
        rev = await repo.create_revision(
            run_id="run-1", org_id="org-1", generation=gen, path=f"docs/{gen}.md", action="update"
        )
        await repo.append_chunk(rev.id, f"gen{gen}")
        await repo.finalize(rev.id)
        gc_seen.append(rev.id)

    for gen in range(KEEP_GENERATIONS + 1, KEEP_GENERATIONS + 3):
        rev = await repo.create_revision(
            run_id="run-1", org_id="org-1", generation=gen, path=f"docs/{gen}.md", action="update"
        )
        await repo.append_chunk(rev.id, f"gen{gen}")
        await repo.finalize(rev.id)

    open_rev = await repo.create_revision(
        run_id="run-1",
        org_id="org-1",
        generation=KEEP_GENERATIONS + 3,
        path="docs/open.md",
        action="update",
    )
    await repo.append_chunk(open_rev.id, "open")

    await repo.gc(run_id="run-1")

    remaining = await repo.list_revisions(run_id="run-1")
    paths = {r.path for r in remaining}
    assert "docs/1.md" not in paths
    assert "docs/2.md" not in paths
    kept_gen = [f"docs/{g}.md" for g in range(4, KEEP_GENERATIONS + 3)]
    assert set(kept_gen) <= paths
    assert "docs/open.md" in paths
