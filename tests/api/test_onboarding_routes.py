"""Tests for onboarding API routes."""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.routes import onboarding


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(onboarding.router)
    from draftly.app.api.auth import get_verified_token
    app.dependency_overrides[get_verified_token] = lambda: {"sub": "tester", "org_id": "test-org"}

    state = MagicMock()
    # Mirror the real composition shape (Task 4a): repositories bundle plus worker.
    repos = state.dependencies.repositories
    repos.onboarding.get = AsyncMock(return_value=None)
    repos.onboarding.upsert = AsyncMock(
        return_value={"org_id": "test-org", "state": "NOT_STARTED"}
    )
    repos.onboarding.mark_step = AsyncMock(
        return_value={"org_id": "test-org", "state": "NOT_STARTED"}
    )
    repos.repository_config.get = AsyncMock(return_value=None)
    repos.repository_config.upsert = AsyncMock(return_value={})
    repos.repository_config.list_by_org = AsyncMock(return_value=[])
    state.worker = MagicMock()
    state.worker.task_runner.has_task = MagicMock(return_value=True)
    state.worker.run_task = AsyncMock(
        return_value={"org_id": "test-org", "state": "COMPLETED", "stages": []}
    )
    app.state.draftly = state

    return TestClient(app)


@contextmanager
def github_connect_patches(
    existing_org: str | None = None,
    token_error: Exception | None = None,
):
    """Patch the collaborators POST /onboarding/github/connect must use.

    Yields a dict of AsyncMocks: update_org, store_installation, get_org.
    """
    stack = ExitStack()
    mocks: dict = {}

    mocks["get_installation_info"] = stack.enter_context(patch(
        "draftly.integrations.github.app_auth.get_installation_info",
        new=AsyncMock(return_value={"account": {"login": "acme"}}),
    ))
    mocks["get_org"] = stack.enter_context(patch(
        "draftly.persistence.repositories.github.get_org_by_github_org",
        new=AsyncMock(
            return_value={"clerk_org_id": existing_org} if existing_org else None
        ),
    ))
    mocks["update_org"] = stack.enter_context(patch(
        "draftly.persistence.repositories.organizations.update_org_github",
        new=AsyncMock(),
    ))
    if token_error is not None:
        mocks["get_installation_token"] = stack.enter_context(patch(
            "draftly.integrations.github.app_auth.get_installation_token",
            new=AsyncMock(side_effect=token_error),
        ))
    else:
        mocks["get_installation_token"] = stack.enter_context(patch(
            "draftly.integrations.github.app_auth.get_installation_token",
            new=AsyncMock(return_value="itok"),
        ))
        mocks["get_repositories"] = stack.enter_context(patch(
            "draftly.integrations.github.app_auth.get_installation_repositories",
            new=AsyncMock(
                return_value=[{"full_name": "acme/api", "id": 7}]
            ),
        ))
    mocks["store_installation"] = stack.enter_context(patch(
        "draftly.persistence.repositories.github.store_github_installation",
        new=AsyncMock(),
    ))

    try:
        yield mocks
    finally:
        stack.close()


class TestOnboardingRouterRegistration:
    def test_onboarding_routes_registered_on_api_app(self) -> None:
        from draftly.app.api.app import create_api_app

        app = create_api_app()
        paths = set(app.openapi()["paths"])
        assert "/api/onboarding/status" in paths
        assert "/api/onboarding/workspace" in paths


class TestOnboardingRoutes:
    def test_get_status_returns_initial_state(self, client: TestClient) -> None:
        response = client.get("/onboarding/status")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "NOT_STARTED"

    def test_workspace_creates_workspace(self, client: TestClient) -> None:
        response = client.post(
            "/onboarding/workspace",
            json={"name": "My Workspace", "description": "Test"},
        )
        assert response.status_code == 200

    def test_github_connect_advances_state(self, client: TestClient) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "WORKSPACE_CREATED",
            "selected_repository": {},
        })
        with github_connect_patches():
            response = client.post(
                "/onboarding/github/connect",
                json={"installation_id": 123},
            )
        assert response.status_code == 200

    def test_github_connect_persists_org_link_and_installation(
        self, client: TestClient
    ) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "WORKSPACE_CREATED",
            "selected_repository": {},
        })
        with github_connect_patches() as mocks:
            response = client.post(
                "/onboarding/github/connect",
                json={"installation_id": 123},
            )
        assert response.status_code == 200
        mocks["update_org"].assert_awaited_once_with(
            org_id="test-org", github_org="acme"
        )
        mocks["store_installation"].assert_awaited_once_with(
            org_id="test-org",
            installation_id=123,
            github_org="acme",
            repositories=[{"full_name": "acme/api", "id": 7}],
        )

    def test_github_connect_rejects_conflicting_org_link(
        self, client: TestClient
    ) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "WORKSPACE_CREATED",
            "selected_repository": {},
        })
        with github_connect_patches(existing_org="other-org"):
            response = client.post(
                "/onboarding/github/connect",
                json={"installation_id": 123},
            )
        assert response.status_code == 409

    def test_github_connect_still_advances_when_repo_fetch_fails(
        self, client: TestClient
    ) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "WORKSPACE_CREATED",
            "selected_repository": {},
        })
        with github_connect_patches(token_error=RuntimeError("gh down")) as mocks:
            response = client.post(
                "/onboarding/github/connect",
                json={"installation_id": 123},
            )
        assert response.status_code == 200
        mocks["store_installation"].assert_awaited_once_with(
            org_id="test-org",
            installation_id=123,
            github_org="acme",
            repositories=[],
        )

    def test_invalid_transition_rejected(self, client: TestClient) -> None:
        # No state row yet → NOT_STARTED → selecting a repository is invalid
        response = client.post(
            "/onboarding/repository",
            json={"full_name": "owner/repo"},
        )
        assert response.status_code == 409

    def test_initialize_rejected_before_preferences(self, client: TestClient) -> None:
        response = client.post("/onboarding/initialize")
        assert response.status_code == 409

    def test_initialize_returns_503_without_worker(self, client: TestClient) -> None:
        client.app.state.draftly.worker = None
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "selected_repository": {"full_name": "owner/repo"},
        })
        response = client.post("/onboarding/initialize")
        assert response.status_code == 503

    def test_initialize_runs_registered_task(self, client: TestClient) -> None:
        state = client.app.state.draftly
        repos = state.dependencies.repositories
        repos.onboarding.get = AsyncMock(side_effect=[
            {"org_id": "test-org", "state": "PREFERENCES_CONFIGURED",
             "selected_repository": {"full_name": "owner/repo"}},   # pre-check
            {"org_id": "test-org", "state": "COMPLETED"},  # post-run read inside workflow
        ])
        repos.onboarding.upsert = AsyncMock(return_value={})
        response = client.post("/onboarding/initialize")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "COMPLETED"
        args = state.worker.run_task.await_args
        assert args.args[0] == "onboarding.initialize"

    def test_complete_rejected_when_required_steps_missing(self, client: TestClient) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "completed_steps": ["workspace"],
        })
        response = client.post("/onboarding/complete")
        assert response.status_code == 409
        assert "github" in response.json()["detail"]

    def test_complete_finalizes_and_is_idempotent(self, client: TestClient) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        record = {
            "org_id": "test-org",
            "state": "COMPLETED",
            "completed_steps": [
                "workspace", "github", "repository", "documentation", "initialization",
            ],
        }
        repos.onboarding.get = AsyncMock(return_value=record)
        assert client.post("/onboarding/complete").status_code == 200
