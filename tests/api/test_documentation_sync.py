"""Tests for documentation sync API routes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from draftly.app.api.routes import documentation


SYNC_RESULT = {
    "document_count": 2,
    "chunk_count": 7,
    "commit_sha": "abc123",
    "baseline": {"commit_sha": "abc123"},
}


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(documentation.router, prefix="/api")
    # Mock auth
    from draftly.app.api.auth import get_verified_token
    app.dependency_overrides[get_verified_token] = lambda: {"sub": "tester", "org_id": "test-org"}

    state = MagicMock()
    # Follows the real composition shape: application.worker + task_runner,
    # and application.dependencies.repositories.jobs (see jobs.py route).
    state.worker = MagicMock()
    state.worker.task_runner.has_task = MagicMock(return_value=True)
    state.worker.run_task = AsyncMock(return_value=SYNC_RESULT)
    state.dependencies.repositories.jobs.get = AsyncMock(
        return_value={"id": "job-123", "status": "completed"}
    )
    state.dependencies.repositories.documents.list_by_org = AsyncMock(return_value=[
        {"repository": "owner/repo", "path": "README.md", "commit_sha": "abc123", "status": "indexed"},
        {"repository": "owner/repo", "path": "docs/guide.md", "commit_sha": "abc123", "status": "indexed"},
        {"repository": "owner/repo", "path": "docs/old.md", "commit_sha": "000000", "status": "stale"},
    ])
    app.state.draftly = state

    return TestClient(app)


class TestDocumentationSyncRoutes:
    def test_sync_runs_task_and_returns_result(self, client: TestClient) -> None:
        response = client.post(
            "/api/documentation/sync",
            json={"repository_full_name": "owner/repo"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"]["document_count"] == 2

    def test_sync_returns_503_when_worker_disabled(self, client: TestClient) -> None:
        client.app.state.draftly.worker = None
        response = client.post(
            "/api/documentation/sync",
            json={"repository_full_name": "owner/repo"},
        )
        assert response.status_code == 503

    def test_sync_returns_404_for_unknown_task(self, client: TestClient) -> None:
        client.app.state.draftly.worker.task_runner.has_task.return_value = False
        response = client.post(
            "/api/documentation/sync",
            json={"repository_full_name": "owner/repo"},
        )
        assert response.status_code == 404

    def test_sync_status_reads_job_record(self, client: TestClient) -> None:
        response = client.get("/api/documentation/sync/job-123")
        assert response.status_code == 200
        assert response.json()["job"]["status"] == "completed"

    def test_sync_status_404_for_unknown_job(self, client: TestClient) -> None:
        client.app.state.draftly.dependencies.repositories.jobs.get = AsyncMock(return_value=None)
        response = client.get("/api/documentation/sync/nope")
        assert response.status_code == 404

    def test_baseline_reports_live_document_state(self, client: TestClient) -> None:
        response = client.get("/api/documentation/baseline?repository=owner/repo")
        assert response.status_code == 200
        data = response.json()
        assert data["document_count"] == 3
        assert data["latest_commit_sha"] == "abc123"
