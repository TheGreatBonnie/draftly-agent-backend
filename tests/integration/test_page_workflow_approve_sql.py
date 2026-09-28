"""Real-Postgres tests for page-workflow approval SQL.

The unit suite drives ``approve_escalated_pages`` through ``FakeClient``, which
interprets the statement in Python. Nothing in the unit suite ever asks Postgres
to *prepare* it, so a statement Postgres refuses to type-check passes the whole
unit suite and then fails the first time a human approves a review. Run
c6d18ea0 hit ``AmbiguousParameterError: could not determine data type of
parameter $3`` there, after the reviewer had already clicked approve.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from draftly.integrations.database.client import DatabaseClient
from draftly.orchestration.page_workflow.repository import NewPage, PageWorkflowRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"),
    reason="NEON_DATABASE_URL not set",
)

MIGRATIONS = Path("src/draftly/persistence/migrations")


async def _scalar(database: DatabaseClient, query: str, *args: object) -> object:
    row = await database.fetch_one(query, *args)
    return None if row is None else row[0]


@pytest.fixture
async def pages():
    """A repository on a real database, with the page-state table present."""
    database = DatabaseClient()
    try:
        if await _scalar(database, "SELECT to_regclass('documentation_page_states')") is None:
            await database.execute(
                (MIGRATIONS / "060_documentation_page_workflow.sql").read_text()
            )
        yield PageWorkflowRepository(database)
    finally:
        await database.close()


async def _seed(pages: PageWorkflowRepository, run_id: str) -> None:
    await pages.database.execute(
        "INSERT INTO workflow_runs (id, org_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        run_id,
        "org-approve-sql",
    )
    await pages.create_pages(
        run_id=run_id,
        org_id="org-approve-sql",
        pages=[NewPage(page_id="docs/a.md", path="docs/a.md", action="update")],
    )
    await _reset_status(pages, run_id)


async def _reset_status(pages: PageWorkflowRepository, run_id: str) -> None:
    await pages.database.execute(
        "UPDATE documentation_page_states SET status = 'awaiting_human_review'"
        " WHERE run_id = $1",
        run_id,
    )


async def _reason(pages: PageWorkflowRepository, run_id: str) -> object:
    return await _scalar(
        pages.database,
        "SELECT escalation_reason FROM documentation_page_states"
        " WHERE run_id = $1 AND page_id = 'docs/a.md'",
        run_id,
    )


@pytest.mark.asyncio
async def test_approve_escalated_pages_prepares_on_postgres(pages) -> None:
    """The approval UPDATE must be type-checkable by a real server.

    Regression: ``$3`` appeared only in ``$3 IS NULL`` and inside ``||``
    concatenations, so Postgres had no context to infer its type and asyncpg
    raised ``AmbiguousParameterError`` while preparing the statement.
    """
    run_id = f"approve-sql-{uuid.uuid4()}"
    await _seed(pages, run_id)

    approved = await pages.approve_escalated_pages(
        run_id=run_id,
        page_ids=["docs/a.md"],
        comment="Looks good",
    )

    assert approved == 1
    assert await _reason(pages, run_id) == "Approved: Looks good"


@pytest.mark.asyncio
async def test_approve_escalated_pages_handles_null_comment_on_postgres(pages) -> None:
    """A ``None`` comment still approves, but leaves no audit trail.

    This is the ``WHEN $3::text IS NULL`` branch — the exact expression
    Postgres could not type, so it is the branch that never once executed in
    production. The transition still happens; only the reason is left alone.
    """
    run_id = f"approve-null-{uuid.uuid4()}"
    await _seed(pages, run_id)

    approved = await pages.approve_escalated_pages(
        run_id=run_id, page_ids=["docs/a.md"], comment=None
    )

    assert approved == 1
    assert await _reason(pages, run_id) is None


@pytest.mark.asyncio
async def test_approve_escalated_pages_appends_repeat_decisions_on_postgres(pages) -> None:
    """Re-approval appends, so the audit trail keeps every decision."""
    run_id = f"approve-repeat-{uuid.uuid4()}"
    await _seed(pages, run_id)

    await pages.approve_escalated_pages(
        run_id=run_id, page_ids=["docs/a.md"], comment="first"
    )
    await _reset_status(pages, run_id)
    await pages.approve_escalated_pages(
        run_id=run_id, page_ids=["docs/a.md"], comment="second"
    )

    assert await _reason(pages, run_id) == "Approved: first\nApproved: second"
