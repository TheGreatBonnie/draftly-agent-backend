"""POST /api/github/review/{run_id} resume-route logging tests."""

from types import SimpleNamespace

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import require_reviewer_role
from draftly.app.api.routes import github as github_routes
from draftly.review.service import ReviewService


def make_app(reviews=None, token_org="org-1"):
    app = FastAPI()
    app.include_router(github_routes.router, prefix="/api")
    app.state.draftly = SimpleNamespace(
        dependencies=SimpleNamespace(
            repositories=SimpleNamespace(reviews=reviews, feedback_outcomes=None),
        )
    )
    app.dependency_overrides[require_reviewer_role] = lambda: {
        "org_id": token_org,
        "user_id": "user-1",
    }
    return TestClient(app)


def review(org_id="org-1", workflow="pull_request"):
    return SimpleNamespace(org_id=org_id, review_id="review-1", status="pending", workflow=workflow)


def test_resume_route_logs_not_found(monkeypatch):
    from structlog.testing import capture_logs

    async def get_none(self, run_id):
        return None

    monkeypatch.setattr(ReviewService, "get_by_run_id", get_none)
    client = make_app(reviews=object())

    with capture_logs() as logs:
        monkeypatch.setattr(
            github_routes, "logger", structlog.get_logger("test.review_resume_404")
        )
        resp = client.post(
    "/api/github/review/run-1",
    json={
        "approved": False,
        "review_id": "review-1",
        "reviewer_id": "user-1",
    },
)

    assert resp.status_code == 404
    markers = [line for line in logs if line.get("event") == "review_resume_not_found"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"


def test_resume_route_logs_org_mismatch(monkeypatch):
    from structlog.testing import capture_logs

    async def get_other(self, run_id):
        return review(org_id="org-other")

    monkeypatch.setattr(ReviewService, "get_by_run_id", get_other)
    client = make_app(reviews=object(), token_org="org-1")

    with capture_logs() as logs:
        monkeypatch.setattr(
            github_routes, "logger", structlog.get_logger("test.review_resume_org")
        )
        resp = client.post(
    "/api/github/review/run-1",
    json={
        "approved": False,
        "review_id": "review-1",
        "reviewer_id": "user-1",
    },
)

    assert resp.status_code == 404
    markers = [line for line in logs if line.get("event") == "review_resume_org_mismatch"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"


def test_resume_route_logs_non_resumable_surface(monkeypatch):
    from structlog.testing import capture_logs

    async def get_pending(self, run_id):
        return review(workflow="unknown_surface")

    monkeypatch.setattr(ReviewService, "get_by_run_id", get_pending)
    client = make_app(reviews=object())

    with capture_logs() as logs:
        monkeypatch.setattr(
            github_routes, "logger", structlog.get_logger("test.review_resume_conflict")
        )
        resp = client.post(
            "/api/github/review/run-1",
            json={
                "approved": True,
                "review_id": "review-1",
                "reviewer_id": "user-1",
            },
        )

    assert resp.status_code == 409
    markers = [line for line in logs if line.get("event") == "review_resume_conflict"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"


def test_resume_route_logs_store_unavailable(monkeypatch):
    from structlog.testing import capture_logs

    client = make_app(reviews=None)

    with capture_logs() as logs:
        monkeypatch.setattr(
            github_routes, "logger", structlog.get_logger("test.review_resume_unavail")
        )
        resp = client.post(
    "/api/github/review/run-1",
    json={
        "approved": False,
        "review_id": "review-1",
        "reviewer_id": "user-1",
    },
)

    assert resp.status_code == 503
    markers = [
        line for line in logs if line.get("event") == "review_resume_store_unavailable"
    ]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"
