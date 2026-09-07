"""Persistence for detected feedback gaps and downstream dispatch state."""

from __future__ import annotations

import json
from typing import Literal

from draftly.feedback.models import DocumentationGapCandidate
from draftly.integrations.database.client import DatabaseClient


class DocumentationGapRepository:
    """Organization-scoped, idempotent documentation-gap repository."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def upsert_candidate(
        self,
        org_id: str,
        candidate: DocumentationGapCandidate,
    ) -> str:
        row = await self.database.fetch_one(
            """
            INSERT INTO documentation_gaps (
                org_id, topic, occurrences, severity, platforms,
                sample_questions, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (org_id, topic)
            DO UPDATE SET
                occurrences = EXCLUDED.occurrences,
                severity = EXCLUDED.severity,
                platforms = EXCLUDED.platforms,
                sample_questions = EXCLUDED.sample_questions,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING gap_id::text
            """,
            org_id,
            candidate.topic,
            candidate.occurrences,
            candidate.severity,
            candidate.platforms,
            json.dumps(candidate.sample_questions),
            json.dumps(candidate.metadata),
        )
        if row is None:
            raise RuntimeError("documentation gap missing after upsert")
        return str(row["gap_id"])

    async def set_outcome(
        self,
        gap_id: str,
        outcome: Literal["documentation", "content", "both", "pending"],
    ) -> None:
        await self.database.execute(
            """
            UPDATE documentation_gaps
            SET outcome = $2, updated_at = now()
            WHERE gap_id = $1
            """,
            gap_id,
            outcome,
        )

    async def mark_dispatched(
        self,
        gap_id: str,
        run_id: str,
        *,
        route: Literal["documentation", "content"],
    ) -> None:
        await self.database.execute(
            """
            UPDATE documentation_gaps
            SET dispatched_at = now(), dispatch_run_id = $2,
                dispatch_route = $3, updated_at = now()
            WHERE gap_id = $1
            """,
            gap_id,
            run_id,
            route,
        )
