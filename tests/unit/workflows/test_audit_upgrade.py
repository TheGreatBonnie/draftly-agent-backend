"""Unit tests for upgraded documentation audit."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from draftly.workflows.documentation.documentation_audit import run_documentation_audit
from draftly.workflows.state import WorkflowStatus


def _documents(rows):
    documents = MagicMock()
    documents.list_by_org = AsyncMock(return_value=rows)
    return documents


@pytest.mark.asyncio
async def test_audit_checks_broken_links():
    context = MagicMock()
    context.repositories.documents = _documents([
        {
            "id": "doc-1",
            "path": "guide.md",
            "content": "# Guide\n\nSee [link](missing-target.md)",
            "updated_at": None,
        },
        {"id": "doc-2", "path": "other.md", "content": "# Other", "updated_at": None},
    ])

    state = await run_documentation_audit(context, org_id="test-org", freshness_days=30)

    assert state.status == WorkflowStatus.DELIVERED
    result = state.result
    assert {"source": "guide.md", "target": "missing-target.md"} in result["broken_links"]
    # Valid links are not flagged
    assert all(b["source"] != "none.md" for b in result["broken_links"])


@pytest.mark.asyncio
async def test_audit_detects_orphaned_documents():
    context = MagicMock()
    context.repositories.documents = _documents([
        {
            "id": "doc-1",
            "path": "index.md",
            "content": "# Index\n\n[Guide](guide.md)",
            "updated_at": None,
        },
        {"id": "doc-2", "path": "guide.md", "content": "# Guide", "updated_at": None},
        {"id": "doc-3", "path": "orphan.md", "content": "# Orphan", "updated_at": None},
    ])

    state = await run_documentation_audit(context, org_id="test-org")

    # orphan.md has no inbound links
    assert "orphan.md" in state.result.get("orphaned_documents", [])
    assert "guide.md" not in state.result.get("orphaned_documents", [])


@pytest.mark.asyncio
async def test_audit_flags_stale_documents():
    old_timestamp = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    context = MagicMock()
    context.repositories.documents = _documents([
        {"id": "old-1", "path": "old.md", "content": "# Old", "updated_at": old_timestamp},
    ])

    state = await run_documentation_audit(context, org_id="test-org", freshness_days=30)

    assert state.result["stale_documents"] == ["old-1"]


@pytest.mark.asyncio
async def test_audit_flags_duplicate_headings_within_a_document():
    context = MagicMock()
    context.repositories.documents = _documents([
        {
            "id": "dup",
            "path": "dup.md",
            "content": "# Guide\n\n## Setup\n\ntext\n\n## Setup\n\nmore",
            "updated_at": None,
        },
    ])

    state = await run_documentation_audit(context, org_id="test-org")

    assert {
        "document": "dup.md",
        "heading": "## Setup",
        "count": 2,
    } in state.result["duplicate_headings"]
