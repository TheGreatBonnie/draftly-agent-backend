from __future__ import annotations

import json
from typing import Any

from draftly.persistence.repositories.reviews import ReviewsRepository


class FakeClient:
    """Mirrors the real client's execute surface (asyncpg style)."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        return None


async def test_store_interrupt_persists_structured_detail() -> None:
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.store_interrupt(
        run_id="ev-1",
        interrupt_id="i-1",
        reason={
            "run_id": "ev-1",
            "summary": "Added PKCE",
            "evaluation": {"faithfulness": 98},
            "evidence_count": 3,
        },
        workflow_type="pull_request",
        org_id="o-1",
    )

    sql, params = client.executed[0]
    assert "INSERT INTO reviews" in sql
    assert "detail" in sql  # the new JSONB column is in the INSERT column list
    # The detail JSON param parses back to the structured dict (not a stringified blob).
    detail_json = params[-1]
    assert json.loads(detail_json)["evaluation"]["faithfulness"] == 98
