"""Overview dashboard API contract."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from draftly.app.api.app import create_api_app
from draftly.app.api.auth import get_verified_token


class EmptyDocuments:
    async def list_by_org(self, *, org_id: str, limit: int) -> list[dict[str, str]]:
        return []


class EmptyReviews:
    async def list_reviews(
        self, *, status: str | None, org_id: str, limit: int
    ) -> list[object]:
        return []


class EmptyEvaluations:
    async def search(
        self, *, org_id: str, evaluation_type: None, limit: int
    ) -> list[dict[str, str]]:
        return []


def test_overview_returns_a_snapshot_for_the_verified_organization() -> None:
    """A missing overview route would return 404 instead of its dashboard contract."""
    app = create_api_app()
    app.dependency_overrides[get_verified_token] = lambda: {"org_id": "org-a"}
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(
                documents=EmptyDocuments(),
                reviews=EmptyReviews(),
                evaluations=EmptyEvaluations(),
            )
        )
    )

    response = TestClient(app).get("/api/overview?days=14")

    assert response.status_code == 200
    assert set(response.json()) == {
        "summary",
        "attention",
        "system",
        "recent_changes",
        "active_workflows",
        "evaluation",
        "activity",
    }
