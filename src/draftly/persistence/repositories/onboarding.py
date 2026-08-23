"""Onboarding state repository."""

from __future__ import annotations

import json
from typing import Any


class OnboardingRepository:
    """CRUD for onboarding_state table."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            from draftly.integrations.database.client import DatabaseClient
            self.client = DatabaseClient()
        else:
            self.client = client

    async def get(self, org_id: str) -> dict[str, Any] | None:
        return await self.client.fetch_one(
            "SELECT * FROM onboarding_state WHERE org_id = $1",
            org_id,
        )

    async def upsert(
        self,
        org_id: str,
        *,
        state: str | None = None,
        completed_steps: list[str] | None = None,
        failure: dict | None = None,
        selected_repository: dict | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if state is not None:
            fields["state"] = state
        if completed_steps is not None:
            fields["completed_steps"] = json.dumps(completed_steps)
        if failure is not None:
            fields["failure"] = json.dumps(failure)
        if selected_repository is not None:
            fields["selected_repository"] = json.dumps(selected_repository)

        if not fields:
            return await self.get(org_id) or {}

        set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields.keys()))
        values = [org_id] + list(fields.values())

        await self.client.execute(
            f"INSERT INTO onboarding_state (org_id, {', '.join(fields.keys())}) "
            f"VALUES ($1, {', '.join(f'${i+2}' for i in range(len(fields)))}) "
            f"ON CONFLICT (org_id) DO UPDATE SET {set_clause}",
            *values,
        )
        return await self.get(org_id) or {"org_id": org_id, "state": state or "NOT_STARTED"}

    async def mark_step(self, org_id: str, step: str) -> dict[str, Any]:
        """Atomically append a step id to completed_steps (JSONB).

        Single-statement upsert instead of get→append→upsert so concurrent
        requests can never drop each other's steps.
        """
        await self.client.execute(
            """
            INSERT INTO onboarding_state (org_id, completed_steps)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (org_id) DO UPDATE SET
                completed_steps = (
                    SELECT COALESCE(jsonb_agg(s), '[]'::jsonb)
                    FROM (
                        SELECT DISTINCT jsonb_array_elements_text(
                            onboarding_state.completed_steps || EXCLUDED.completed_steps
                        ) AS s
                    ) dedup
                )
            """,
            org_id,
            json.dumps([step]),
        )
        return await self.get(org_id) or {"org_id": org_id, "completed_steps": [step]}

    async def mark_failed(self, org_id: str, step: str, detail: dict) -> dict[str, Any]:
        return await self.upsert(org_id, state="FAILED", failure={"step": step, **detail})
