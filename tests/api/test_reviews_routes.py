"""Review queue API: pending list + detail, org-scoped."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.reviews import router
from draftly.persistence.repositories.reviews import ReviewRecord


def record(rid: str = "rev-1", org_id: str = "org-1", status: str = "pending") -> ReviewRecord:
    return ReviewRecord(
        id=rid,
        org_id=org_id,
        thread_id="evt-9",
        workflow="documentation",
        tool_name="doc-review",
        tool_args={"interrupt_id": "i-1"},
        action_description="docs update",
        status=status,
        created_at=datetime.now(UTC),
    )


class FakeRepo:
    def __init__(self) -> None:
        self.rows = [record()]
        self.calls: list[dict] = []

    async def list_reviews(self, *, status=None, org_id=None, limit=100):
        self.calls.append({"status": status, "org_id": org_id, "limit": limit})
        rows = self.rows
        if status:
            rows = [r for r in rows if r.status == status]
        if org_id:
            rows = [r for r in rows if r.org_id == org_id]
        return rows[:limit]

    async def get_review(self, review_id: str):
        return next((r for r in self.rows if r.id == review_id), None)


def make_app(repo: FakeRepo) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "sub": "user-1",
        "org_id": "org-1",
    }
    app.state.draftly = type(
        "State",
        (),
        {
            "dependencies": type(
                "Deps", (), {"repositories": type("R", (), {"reviews": repo})()}
            )()
        },
    )()
    return app


def test_lists_pending_scoped_to_org() -> None:
    repo = FakeRepo()
    client = TestClient(make_app(repo))

    resp = client.get("/reviews", params={"status": "pending"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["id"] == "rev-1"
    assert body["items"][0]["run_id"] == "evt-9"
    assert body["items"][0]["interrupt_id"] == "i-1"
    assert "display" in body["items"][0]
    assert repo.calls[0]["status"] == "pending"
    assert repo.calls[0]["org_id"] == "org-1"
    assert repo.calls[0]["limit"] <= 200


def test_detail_404_for_unknown_or_other_org() -> None:
    repo = FakeRepo()
    client = TestClient(make_app(repo))
    assert client.get("/reviews/nope").status_code == 404

    other_org = FakeRepo()
    other_org.rows = [record(org_id="org-2")]
    other_client = TestClient(make_app(other_org))
    assert other_client.get("/reviews/rev-1").status_code == 404


def test_detail_returns_review_shape() -> None:
    client = TestClient(make_app(FakeRepo()))
    resp = client.get("/reviews/rev-1")
    assert resp.status_code == 200
    body = resp.json()["review"]
    assert body["workflow"] == "documentation"
    assert body["tool_name"] == "doc-review"
    assert body["action_description"] == "docs update"
    assert "display" in body
