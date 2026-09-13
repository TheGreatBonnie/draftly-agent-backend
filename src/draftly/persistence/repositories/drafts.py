"""Draft store persistence: writer file bytes live here, never in a model output.

The documentation writer used to inline full markdown in a single
``DocChangePlan`` structured-output JSON (schemas.py). Past a size threshold the
streaming parser truncated the payload (`failed to parse tool input json,
defaulting to empty dict`), patched by a 2-file plan cap in plan_guard.py. The
draft store is the architectural fix: the writer streams bytes through small
``append_chunk`` tool calls into ``draft_chunks``, and ``draft_revisions`` owns
the immutable, versioned file metadata. Consumers (evaluate, review gate,
delivery) read the latest sealed generation from here — the drafts repo is the
single source of truth for proposed bytes.

Generations: each writer node execution opens at most one generation (see the
graph's NextGenerationHook). A generation is immutable once its revision is
sealed; rejections start a NEW generation instead of mutating. ``KEEP_GENERATIONS``
bounds how many sealed generations are retained on ``gc`` (default 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient

KEEP_GENERATIONS = 3


@dataclass
class DraftRevision:
    """One immutable draft file row in ``draft_revisions``."""

    id: str
    run_id: str
    org_id: str
    generation: int
    path: str
    action: str
    sealed: bool = False
    content_size: int = 0
    created_at: datetime | None = None
    sealed_at: datetime | None = None

    @property
    def content(self) -> str:
        return ""


@dataclass
class DraftFile:
    """Assembled, sealed draft content for one path (read model)."""

    path: str
    action: str
    content: str
    content_size: int


class DraftRepository:
    """Persistence for streamed documentation drafts (data plane).

    Mirror of ``reviews.py``: one ``DatabaseClient``, asyncpg-style calls.
    ``append_chunk``/``finalize`` raise ``ValueError`` instead of mutating a
    sealed revision — callers surface the error to the steering judge.
    """

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def create_revision(
        self,
        *,
        run_id: str,
        org_id: str,
        generation: int,
        path: str,
        action: str,
    ) -> DraftRevision:
        draft_id = str(uuid4())
        now = datetime.now(UTC)
        await self.database.execute(
            """
            INSERT INTO draft_revisions (
                id, run_id, org_id, generation, path, action, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            draft_id,
            run_id,
            org_id,
            generation,
            path,
            action,
            now,
        )
        return DraftRevision(
            id=draft_id,
            run_id=run_id,
            org_id=org_id,
            generation=generation,
            path=path,
            action=action,
            sealed=False,
            content_size=0,
            created_at=now,
        )

    async def _get_revision(self, draft_id: str) -> DraftRevision | None:
        row = await self.database.fetch_one(
            """
            SELECT * FROM draft_revisions WHERE id = $1
            """,
            draft_id,
        )
        if row is None:
            return None
        return self._to_revision(row)

    @staticmethod
    def _to_revision(row: Any) -> DraftRevision:
        return DraftRevision(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            org_id=str(row["org_id"]),
            generation=int(row["generation"]),
            path=str(row["path"]),
            action=str(row["action"]),
            sealed=bool(row["sealed"]),
            content_size=int(row["content_size"] or 0),
            created_at=row.get("created_at"),
            sealed_at=row.get("sealed_at"),
        )

    async def append_chunk(self, draft_id: str, content: str) -> int:
        """Append a content chunk; returns the chunk count so far."""
        revision = await self._get_revision(draft_id)
        if revision is None:
            raise ValueError(f"unknown draft_id {draft_id!r}")
        if revision.sealed:
            raise ValueError(
                f"draft {draft_id!r} is already sealed; start a new draft, do not mutate"
            )
        chunk_index = await self._next_chunk_index(draft_id)
        await self.database.execute(
            """
            INSERT INTO draft_chunks (draft_id, chunk_index, content) VALUES ($1, $2, $3)
            """,
            draft_id,
            chunk_index,
            content,
        )
        return chunk_index + 1

    async def _next_chunk_index(self, draft_id: str) -> int:
        rows = await self.database.fetch_all(
            "SELECT chunk_index FROM draft_chunks WHERE draft_id = $1",
            draft_id,
        )
        if not rows:
            return 0
        return max(int(r["chunk_index"]) for r in rows) + 1

    async def finalize(self, draft_id: str) -> DraftRevision:
        """Seal a revision: immutable after this point."""
        revision = await self._get_revision(draft_id)
        if revision is None:
            raise ValueError(f"unknown draft_id {draft_id!r}")
        if revision.sealed:
            raise ValueError(f"draft {draft_id!r} is already sealed")
        content = await self._assembled(draft_id)
        size = len(content)
        now = datetime.now(UTC)
        await self.database.execute(
            """
            UPDATE draft_revisions
               SET sealed = TRUE, content_size = $2
             WHERE id = $1
            """,
            draft_id,
            size,
        )
        await self.database.execute(
            """
            UPDATE draft_revisions SET sealed_at = $2 WHERE id = $1 AND sealed = TRUE
            """,
            draft_id,
            now,
        )
        return DraftRevision(
            id=revision.id,
            run_id=revision.run_id,
            org_id=revision.org_id,
            generation=revision.generation,
            path=revision.path,
            action=revision.action,
            sealed=True,
            content_size=size,
            created_at=revision.created_at,
            sealed_at=now,
        )

    async def _assembled(self, draft_id: str) -> str:
        rows = await self._chunks(draft_id)
        return "".join(str(r["content"]) for r in rows)

    async def _chunks(self, draft_id: str) -> list[Any]:
        return await self.database.fetch_all(
            """
            SELECT draft_id, chunk_index, content
              FROM draft_chunks
             WHERE draft_id = $1
             ORDER BY chunk_index
            """,
            draft_id,
        )

    async def get_generation(self, *, run_id: str, generation: int) -> list[DraftFile]:
        """Sealed revisions of one generation with assembled content."""
        rows = await self.database.fetch_all(
            """
            SELECT * FROM draft_revisions
             WHERE run_id = $1 AND generation = $2
             ORDER BY path
            """,
            run_id,
            generation,
        )
        files: list[DraftFile] = []
        for row in rows:
            revision = self._to_revision(row)
            if not revision.sealed:
                continue
            content = await self._assembled(revision.id)
            files.append(
                DraftFile(
                    path=revision.path,
                    action=revision.action,
                    content=content,
                    content_size=revision.content_size,
                )
            )
        return files

    async def get_latest(self, *, run_id: str) -> list[DraftFile]:
        """Highest sealed generation for a run (the authoritative snapshot)."""
        rows = await self.database.fetch_all(
            """
            SELECT * FROM draft_revisions
             WHERE run_id = $1
             ORDER BY generation DESC, path
            """,
            run_id,
        )
        latest_generation: int | None = None
        for row in rows:
            revision = self._to_revision(row)
            if revision.sealed:
                latest_generation = revision.generation
                break
        if latest_generation is None:
            return []
        return await self.get_generation(run_id=run_id, generation=latest_generation)

    async def list_revisions(
        self,
        *,
        run_id: str,
        generation: int | None = None,
    ) -> list[DraftRevision]:
        if generation is None:
            rows = await self.database.fetch_all(
                """
                SELECT * FROM draft_revisions
                 WHERE run_id = $1
                 ORDER BY generation, path
                """,
                run_id,
            )
        else:
            rows = await self.database.fetch_all(
                """
                SELECT * FROM draft_revisions
                 WHERE run_id = $1 AND generation = $2
                 ORDER BY path
                """,
                run_id,
                generation,
            )
        return [self._to_revision(r) for r in rows]

    async def next_generation(self, *, run_id: str) -> int:
        row = await self.database.fetch_one(
            "SELECT MAX(generation) AS generation FROM draft_revisions WHERE run_id = $1",
            run_id,
        )
        if row is None or row["generation"] is None:
            return 1
        return int(row["generation"]) + 1

    async def gc(self, *, run_id: str) -> None:
        """Delete sealing generations older than the newest ``KEEP_GENERATIONS``.

        Never deletes the current (open) generation: the newest generation is
        kept whole even if it contains an unsealed revision.
        """
        revisions = await self.list_revisions(run_id=run_id)
        if not revisions:
            return
        newest = max(r.generation for r in revisions)
        doomed = [
            r
            for r in revisions
            if r.sealed and r.generation <= newest - KEEP_GENERATIONS
        ]
        if not doomed:
            return
        doomed_ids = tuple(r.id for r in doomed)
        placeholders = ", ".join(f"${i+1}" for i in range(len(doomed_ids)))
        await self.database.execute(
            f"DELETE FROM draft_chunks WHERE draft_id IN ({placeholders})",
            *doomed_ids,
        )
        await self.database.execute(
            f"DELETE FROM draft_revisions WHERE id IN ({placeholders})",
            *doomed_ids,
        )
