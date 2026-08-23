"""Unit tests for onboarding state repository."""

import pytest
from dataclasses import dataclass, field
from typing import Any

from draftly.persistence.repositories.onboarding import OnboardingRepository


@dataclass
class FakeClient:
    responses: list[Any] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)

    async def fetch_one(self, query, *args):
        self.calls.append(("fetch_one", query, args))
        return self.responses.pop(0) if self.responses else None

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        if self.responses:
            self.responses.pop(0)
        return "OK"


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found():
    client = FakeClient(responses=[None])
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_state_when_found():
    client = FakeClient(responses=[{
        "org_id": "org-123",
        "state": "WORKSPACE_CREATED",
        "completed_steps": ["workspace"],
        "failure": None,
        "selected_repository": None,
    }])
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert result is not None
    assert result["state"] == "WORKSPACE_CREATED"


@pytest.mark.asyncio
async def test_upsert_inserts_new_state():
    client = FakeClient(responses=[None, "OK"])
    repo = OnboardingRepository(client)
    await repo.upsert("org-123", state="NOT_STARTED")
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_mark_step_adds_step_atomically():
    # Atomic single-statement append: one execute, then a read-back.
    client = FakeClient(responses=[
        "OK",  # execute: INSERT ... ON CONFLICT DO UPDATE (JSONB merge)
        {"org_id": "org-123", "completed_steps": ["workspace", "github"]},
    ])
    repo = OnboardingRepository(client)
    result = await repo.mark_step("org-123", "github")
    assert "github" in (result.get("completed_steps") or [])
    sql, _args = client.calls[0][1:]
    assert "ON CONFLICT (org_id)" in sql
    assert "EXCLUDED.completed_steps" in sql
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_mark_failed_sets_failure():
    client = FakeClient(responses=[None, "OK"])
    repo = OnboardingRepository(client)
    await repo.mark_failed("org-123", "initialize", {"detail": "auth failed"})
    assert len(client.calls) == 2
