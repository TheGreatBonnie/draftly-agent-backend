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
            "classification": {"urgency": "high"},
            "evidence": [{"id": "src/auth/oauth.py:10"}],
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
    assert json.loads(detail_json)["classification"]["urgency"] == "high"
    assert json.loads(detail_json)["evidence"][0]["id"] == "src/auth/oauth.py:10"


async def test_store_interrupt_persists_document_payload() -> None:
    """The gate's document payload must survive into the persisted detail
    JSONB so the review detail page can render the proposed content."""
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.store_interrupt(
        run_id="ev-2",
        interrupt_id="i-2",
        reason={
            "run_id": "ev-2",
            "summary": "Updated widgets guide",
            "evaluation": {},
            "evidence_count": 2,
            "document": {
                "repository": "acme/api",
                "files": [{"path": "docs/widgets.md", "content": "# Widgets", "action": "update"}],
                "commit_message": "docs: update widgets",
                "summary": "Updated widgets guide",
            },
        },
        workflow_type="pull_request",
        org_id="o-1",
    )

    _, params = client.executed[0]
    detail = json.loads(params[-1])
    assert detail["document"]["files"][0]["path"] == "docs/widgets.md"
    assert detail["document"]["files"][0]["content"] == "# Widgets"


async def test_store_interrupt_action_description_never_leaks_run_id() -> None:
    """Without a summary, the stored action_description must read like a
    human notice instead of exposing the internal run id."""
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.store_interrupt(
        run_id="ev-3",
        interrupt_id="i-3",
        reason={"run_id": "ev-3", "summary": "", "evaluation": {}, "evidence_count": 0},
        workflow_type="pull_request",
        org_id="o-1",
    )

    _, params = client.executed[0]
    assert params[5] == "a documentation review is pending"
    detail = json.loads(params[-1])
    assert detail["summary"] == ""


async def test_store_interrupt_persists_changelog() -> None:
    """The gate's changelog payload must survive into the persisted detail
    JSONB so the review detail page can render the proposed entry."""
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.store_interrupt(
        run_id="ev-4",
        interrupt_id="i-4",
        reason={
            "run_id": "ev-4",
            "summary": "Release v1.1.0",
            "evaluation": {},
            "evidence_count": 2,
            "document": {
                "repository": "acme/api",
                "files": [{"path": "docs/whats-new.md", "action": "create"}],
                "commit_message": "docs: what's new in v1.1.0",
            },
            "changelog": {
                "version": "v1.1.0",
                "date": "2026-09-04",
                "entries": [{"category": "Added", "text": "OAuth login"}],
                "raw_markdown": "## [v1.1.0] - 2026-09-04\n### Added\n- OAuth login",
            },
        },
        workflow_type="pull_request",
        org_id="o-1",
    )

    _, params = client.executed[0]
    detail = json.loads(params[-1])
    assert detail["changelog"]["raw_markdown"].startswith("## [v1.1.0]")
    assert detail["changelog"]["entries"] == [{"category": "Added", "text": "OAuth login"}]
