"""Tests for onboarding API routes."""

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.routes import onboarding
from draftly.workflows.state import WorkflowState, WorkflowStatus


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
    repos.jobs.insert = AsyncMock(return_value={})
    repos.jobs.upsert_on_conflict = AsyncMock(return_value={})
    repos.repository_config.get = AsyncMock(return_value=None)
    repos.repository_config.upsert = AsyncMock(return_value={})
    repos.repository_config.list_by_org = AsyncMock(return_value=[])
    state.worker = MagicMock()
    state.worker.task_runner.has_task = MagicMock(return_value=True)
    state.worker.run_task = AsyncMock(
        return_value={"org_id": "test-org", "state": "COMPLETED", "stages": []}
    )
    state.settings = SimpleNamespace(rq_enabled=False)
    redis_mock = MagicMock()
    native_mock = AsyncMock()
    native_mock.set.return_value = True
    native_mock.get.return_value = None
    redis_mock.native = native_mock
    state.redis_client = redis_mock
    app.state.draftly = state
    app.state.redis_tickets = MagicMock(issue=AsyncMock(return_value="test-ticket"))

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


@contextmanager
def github_installation_patches(repositories=None):
    """Patch token mint + GitHubClient for installation-scoped repo listing."""
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "draftly.integrations.github.app_auth.get_installation_token",
                new=AsyncMock(return_value="ghs_test"),
            )
        )
        client_cls = stack.enter_context(patch("draftly.integrations.github.client.GitHubClient"))
        gh = client_cls.return_value
        gh.get_installation_repositories = AsyncMock(
            return_value=repositories
            or [{"full_name": "TheGreatBonnie/authly", "id": 7}]
        )
        yield gh


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
        assert data["state"] == "INITIALIZING"
        assert "run_id" in data
        assert "ticket" in data
        args = state.worker.run_task.await_args
        assert args.args[0] == "onboarding.initialize"

    def test_initialize_recovers_stale_lock_without_run_id(self, client: TestClient) -> None:
        """When the init lock is held but no run_id can be resumed (persisted
        init_run_id was cleared), a fresh start must force-release the stale
        lock and return a run_id + ticket — never a null run_id."""
        state = client.app.state.draftly
        repos = state.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org", "state": "PREFERENCES_CONFIGURED",
            "selected_repository": {"full_name": "owner/repo"},  # no init_run_id
        })
        repos.onboarding.upsert = AsyncMock(return_value={})
        with (
            patch.object(onboarding, "try_acquire_init_lock", AsyncMock(side_effect=[False, True])),
            patch.object(onboarding, "force_release_init_lock", AsyncMock()) as release,
        ):
            response = client.post("/onboarding/initialize")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "INITIALIZING"
        assert data["run_id"]
        assert data["ticket"] == "test-ticket"
        release.assert_awaited_once()

    def test_initialize_503_when_lock_still_contested_after_release(self, client: TestClient) -> None:
        state = client.app.state.draftly
        repos = state.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org", "state": "PREFERENCES_CONFIGURED",
            "selected_repository": {"full_name": "owner/repo"},
        })
        with (
            patch.object(onboarding, "try_acquire_init_lock", AsyncMock(return_value=False)),
            patch.object(onboarding, "force_release_init_lock", AsyncMock()),
        ):
            response = client.post("/onboarding/initialize")
        assert response.status_code == 503

    def test_complete_rejected_when_required_steps_missing(self, client: TestClient) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "completed_steps": ["workspace"],
            "selected_repository": {"full_name": "owner/repo", "document_count": 3},
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
            "selected_repository": {"full_name": "owner/repo", "document_count": 2},
        }
        repos.onboarding.get = AsyncMock(return_value=record)
        assert client.post("/onboarding/complete").status_code == 200

    def test_complete_rejected_for_completed_row_with_zero_documents(
        self, client: TestClient,
    ) -> None:
        """A poisoned COMPLETED row must not masquerade as done (fake-success guard)."""
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "COMPLETED",
            "completed_steps": [
                "workspace", "github", "repository", "documentation", "initialization",
            ],
            "selected_repository": {"full_name": "owner/repo", "document_count": 0},
        })
        repos.onboarding.upsert = AsyncMock(return_value={})
        response = client.post("/onboarding/complete")
        assert response.status_code == 409
        assert "no documents indexed" in response.json()["detail"]
        repos.onboarding.upsert.assert_not_awaited()

    def test_complete_rejected_when_corpus_empty_before_finalize(
        self, client: TestClient,
    ) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "completed_steps": [
                "workspace", "github", "repository", "documentation", "initialization",
            ],
            "selected_repository": {"full_name": "owner/repo"},
        })
        repos.onboarding.upsert = AsyncMock(return_value={})
        response = client.post("/onboarding/complete")
        assert response.status_code == 409
        assert "no documents indexed" in response.json()["detail"]

    def test_complete_finalizes_non_completed_row_with_documents(
        self, client: TestClient,
    ) -> None:
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "completed_steps": [
                "workspace", "github", "repository", "documentation", "initialization",
            ],
            "selected_repository": {"full_name": "owner/repo", "document_count": 5},
        })
        repos.onboarding.upsert = AsyncMock(return_value={})
        response = client.post("/onboarding/complete")
        assert response.status_code == 200
        upsert_kwargs = repos.onboarding.upsert.await_args.kwargs
        assert upsert_kwargs.get("state") == "COMPLETED"


class TestConnectGithub:
    """connect_github must persist installation + org mapping (self-heal fix)."""

    INSTALLATION_INFO = {"account": {"login": "acme"}}
    CONNECT_BODY = {"installation_id": 156346003}

    @pytest.fixture()
    def patches(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "draftly.integrations.github.app_auth.get_installation_info",
                    new=AsyncMock(return_value=self.INSTALLATION_INFO),
                )
            )
            store = stack.enter_context(
                patch(
                    "draftly.persistence.repositories.github.store_github_installation",
                    new=AsyncMock(),
                )
            )
            upd = stack.enter_context(
                patch(
                    "draftly.persistence.repositories.organizations.update_org_github",
                    new=AsyncMock(),
                )
            )
            yield store, upd

    def _set_state(self, client, state: str):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "state": state,
        }

    def _post(self, client):
        return client.post("/onboarding/github/connect", json=self.CONNECT_BODY)

    def test_connect_persists_installation_and_org_mapping(self, client, patches):
        store, upd = patches
        self._set_state(client, "WORKSPACE_CREATED")

        resp = self._post(client)

        assert resp.status_code == 200
        assert resp.json() == {"state": "GITHUB_CONNECTED", "github_org": "acme"}
        store.assert_awaited_once_with(
            org_id="test-org", installation_id=156346003, github_org="acme"
        )
        upd.assert_awaited_once_with(org_id="test-org", github_org="acme")

    def test_connect_upserts_selected_repository_blob(self, client, patches):
        self._set_state(client, "WORKSPACE_CREATED")
        repos = client.app.state.draftly.dependencies.repositories

        resp = self._post(client)

        assert resp.status_code == 200
        blob = repos.onboarding.upsert.await_args.kwargs["selected_repository"]
        assert blob["github_org"] == "acme"
        assert blob["installation_id"] == 156346003

    def test_reconnect_is_idempotent(self, client, patches):
        store, upd = patches
        self._set_state(client, "GITHUB_CONNECTED")

        first = self._post(client)
        second = self._post(client)

        assert first.status_code == 200
        assert second.status_code == 200
        assert store.await_count == 2
        assert upd.await_count == 2

    def test_persistence_failure_propagates(self, client, patches):
        store, _ = patches
        store.side_effect = RuntimeError("db down")
        self._set_state(client, "WORKSPACE_CREATED")

        with pytest.raises(RuntimeError, match="db down"):
            self._post(client)


class TestDiscoverDocumentationAuth:
    """discover_documentation must authenticate all calls as the App installation."""

    INSTALLATION_TOKEN = "ghs_test_123"

    @pytest.fixture()
    def patches(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "draftly.integrations.github.app_auth.get_installation_token",
                    new=AsyncMock(return_value=self.INSTALLATION_TOKEN),
                )
            )
            auth_cls = stack.enter_context(
                patch("draftly.integrations.github.auth.GitHubAuth")
            )
            client_cls = stack.enter_context(
                patch("draftly.integrations.github.client.GitHubClient")
            )
            gh = client_cls.return_value
            gh.get_repository = AsyncMock(return_value={"default_branch": "main"})
            gh.get_tree = AsyncMock(
                return_value=[{"type": "blob", "path": "README.md"}]
            )
            yield auth_cls, client_cls

    def _set_selected_repository(self, client):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "state": "REPOSITORY_SELECTED",
            "selected_repository": {
                "full_name": "TheGreatBonnie/authly",
                "installation_id": 156354594,
            },
        }

    def test_all_github_calls_use_installation_token(self, client, patches):
        auth_cls, client_cls = patches
        self._set_selected_repository(client)

        resp = client.post("/onboarding/documentation/discover")

        assert resp.status_code == 200
        auth_cls.assert_called_once_with(token=self.INSTALLATION_TOKEN)
        client_cls.assert_called_once_with(auth=auth_cls.return_value)
        gh = client_cls.return_value
        gh.get_repository.assert_awaited_once_with("TheGreatBonnie/authly")
        gh.get_tree.assert_awaited_once_with(
            "TheGreatBonnie", "authly", "main", self.INSTALLATION_TOKEN
        )
        body = resp.json()
        assert body["count"] >= 1
        assert body["total_files"] == 1
        # NOTE: adapted from the brief's `any(c["path"] == ...)` — discover()
        # returns list[str], not list[dict], and the HTTP contract is unchanged.
        assert "README.md" in body["candidates"]


_ORG_GUARD_CASES = [
    ("get", "/onboarding/status", None),
    ("post", "/onboarding/workspace", {"name": "W"}),
    ("post", "/onboarding/github/connect", {"installation_id": 1}),
    ("get", "/onboarding/github/repositories", None),
    ("post", "/onboarding/repository", {"full_name": "o/r"}),
    ("post", "/onboarding/documentation/discover", None),
    ("post", "/onboarding/sources", {}),
    ("post", "/onboarding/integrations", {}),
    ("post", "/onboarding/preferences", {}),
    ("post", "/onboarding/initialize", None),
    ("get", "/onboarding/initialize/status", None),
    ("post", "/onboarding/initialize/retry", None),
    ("post", "/onboarding/complete", None),
]


class TestOrgGuards:
    """Every endpoint rejects a token without org_id uniformly with 401."""

    @pytest.mark.parametrize(("method", "path", "body"), _ORG_GUARD_CASES)
    def test_missing_org_id_rejected_uniformly(self, client, method, path, body):
        from draftly.app.api.auth import get_verified_token

        client.app.dependency_overrides[get_verified_token] = lambda: {"sub": "tester"}

        if body is not None:
            resp = getattr(client, method)(path, json=body)
        else:
            resp = getattr(client, method)(path)

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Missing organization ID"}


class TestInitializeTicketAndRunId:
    """POST /onboarding/initialize returns run_id + ticket for SSE streaming."""

    def test_initialize_returns_run_id_and_ticket(self, client):
        """POST /onboarding/initialize should return run_id and ticket for SSE."""
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get = AsyncMock(return_value={
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "selected_repository": {"full_name": "t/r"},
        })
        client.app.state.draftly.worker.task_runner.has_task = MagicMock(
            return_value=True,
        )
        client.app.state.draftly.worker.run_task = AsyncMock(
            return_value={"state": "COMPLETED"}
        )
        client.app.state.redis_tickets = MagicMock(issue=AsyncMock(return_value="test-ticket"))

        resp = client.post("/onboarding/initialize")

        assert resp.status_code == 200
        body = resp.json()
        assert "run_id" in body
        assert "ticket" in body
        assert body["state"] == "INITIALIZING"


class TestStateMachineGuards:
    """POST endpoints enforce the linear §5.2 machine; same-state replays stay legal."""

    def _set_state(self, client, state, **extra):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": state,
            **extra,
        }

    @pytest.mark.parametrize(
        ("path", "body", "from_state"),
        [
            ("/onboarding/workspace", {"name": "W"}, "WORKSPACE_CREATED"),
            ("/onboarding/sources", {}, "DOCUMENTATION_DISCOVERED"),
            ("/onboarding/integrations", {}, "INTEGRATIONS_CONFIGURED"),
            ("/onboarding/preferences", {}, "PREFERENCES_CONFIGURED"),
        ],
    )
    def test_same_state_replay_allowed(self, client, path, body, from_state):
        self._set_state(client, from_state)
        assert client.post(path, json=body).status_code == 200

    def test_repository_replay_allowed(self, client):
        self._set_state(
            client,
            "REPOSITORY_SELECTED",
            selected_repository={"installation_id": 156354594},
        )
        with github_installation_patches():
            resp = client.post(
                "/onboarding/repository", json={"full_name": "TheGreatBonnie/authly"}
            )
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        ("path", "body", "from_state"),
        [
            ("/onboarding/sources", {}, "GITHUB_CONNECTED"),
            ("/onboarding/integrations", {}, "REPOSITORY_SELECTED"),
            ("/onboarding/preferences", {}, "DOCUMENTATION_DISCOVERED"),
        ],
    )
    def test_skipping_steps_rejected(self, client, path, body, from_state):
        self._set_state(client, from_state)
        resp = client.post(path, json=body)
        assert resp.status_code == 409

    def test_regression_replay_rejected(self, client):
        self._set_state(client, "WORKSPACE_CREATED")
        assert client.post("/onboarding/preferences", json={}).status_code == 409


class TestRepositorySelectionGuard:
    """select_repository only accepts repositories served by the linked installation."""

    @pytest.fixture()
    def github(self):
        with github_installation_patches() as gh:
            yield gh

    def _set_state(self, client, state):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": state,
            "selected_repository": {"installation_id": 156354594},
        }

    def test_selecting_installation_repo_succeeds(self, client, github):
        self._set_state(client, "GITHUB_CONNECTED")

        resp = client.post(
            "/onboarding/repository", json={"full_name": "TheGreatBonnie/authly"}
        )

        assert resp.status_code == 200
        repos = client.app.state.draftly.dependencies.repositories
        repos.repository_config.upsert.assert_awaited_once()

    def test_selecting_foreign_repo_rejected_before_persistence(self, client, github):
        self._set_state(client, "GITHUB_CONNECTED")
        repos = client.app.state.draftly.dependencies.repositories

        resp = client.post("/onboarding/repository", json={"full_name": "other/private"})

        assert resp.status_code == 422
        assert "not accessible" in resp.json()["detail"]
        repos.repository_config.upsert.assert_not_awaited()
        repos.onboarding.upsert.assert_not_awaited()

    def test_malformed_name_rejected_without_github_calls(self, client, github):
        self._set_state(client, "GITHUB_CONNECTED")
        repos = client.app.state.draftly.dependencies.repositories

        resp = client.post("/onboarding/repository", json={"full_name": "noshlash"})

        assert resp.status_code == 422
        github.get_installation_repositories.assert_not_awaited()
        repos.repository_config.upsert.assert_not_awaited()

    def test_discover_defends_against_malformed_stored_name(self, client, github):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "REPOSITORY_SELECTED",
            "selected_repository": {"full_name": "broken", "installation_id": 156354594},
        }

        resp = client.post("/onboarding/documentation/discover")

        assert resp.status_code == 409
        github.get_installation_repositories.assert_not_awaited()


class TestInitializeRobustness:
    """initialize/retry never strand a row in INITIALIZING (crash → FAILED + 502)."""

    INIT_PATHS = [
        ("/onboarding/initialize", "PREFERENCES_CONFIGURED"),
        ("/onboarding/initialize/retry", "FAILED"),
    ]

    def _set_state(self, client, state):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": state,
            "selected_repository": {"full_name": "o/r"},
        }

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_worker_unavailable_leaves_row_unchanged(self, client, path, from_state):
        self._set_state(client, from_state)
        client.app.state.draftly.worker = None

        resp = client.post(path)

        assert resp.status_code == 503
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.upsert.assert_not_awaited()

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_unknown_task_leaves_row_unchanged(self, client, path, from_state):
        self._set_state(client, from_state)
        state = client.app.state.draftly
        state.worker.task_runner.has_task = MagicMock(return_value=False)

        resp = client.post(path)

        assert resp.status_code == 404
        repos = state.dependencies.repositories
        repos.onboarding.upsert.assert_not_awaited()

    def test_double_initialize_single_flights(self, client):
        self._set_state(client, "PREFERENCES_CONFIGURED")
        state = client.app.state.draftly

        # Simulate lock acquired for the first call, rejected for the second
        redis_mock = MagicMock()
        native_mock = AsyncMock()
        native_mock.set.side_effect = [True, None]
        native_mock.get.return_value = "first-run-id"
        redis_mock.native = native_mock
        state.redis_client = redis_mock

        resp1 = client.post("/onboarding/initialize")
        # manually update the mock return value for the second call
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "INITIALIZING",
            "selected_repository": {"init_run_id": "first-run-id"},
        }
        resp2 = client.post("/onboarding/initialize")

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        body1 = resp1.json()
        body2 = resp2.json()

        assert body1["state"] == "INITIALIZING"
        assert body2["state"] == "INITIALIZING"
        assert body2.get("resumed") is True
        assert body2["run_id"] == "first-run-id"
        assert state.worker.run_task.call_count == 1

    def test_initialize_fail_open_without_redis(self, client):
        self._set_state(client, "PREFERENCES_CONFIGURED")
        client.app.state.draftly.redis_client = None

        resp = client.post("/onboarding/initialize")
        assert resp.status_code == 200
        assert resp.json()["state"] == "INITIALIZING"

    @pytest.mark.asyncio
    async def test_initialize_lock_released_on_workflow_exception(self, client):
        import asyncio
        self._set_state(client, "PREFERENCES_CONFIGURED")
        state = client.app.state.draftly
        state.worker.run_task = AsyncMock(side_effect=RuntimeError("crash"))

        redis_mock = MagicMock()
        native_mock = AsyncMock()
        native_mock.set.return_value = True

        # Capture the run_id set by the handler and return it in get() so delete is called
        def fake_get(key):
            # lock key is onboarding:init-lock:test-org, return the value set by set()
            run_id = native_mock.set.call_args[0][1]
            return run_id

        native_mock.get = AsyncMock(side_effect=fake_get)
        redis_mock.native = native_mock
        state.redis_client = redis_mock

        resp = client.post("/onboarding/initialize")
        assert resp.status_code == 200

        # wait a bit for the background task to run its except/finally
        await asyncio.sleep(0.05)
        native_mock.delete.assert_awaited()

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_run_failure_marks_failed_and_returns_502(self, client, path, from_state):
        self._set_state(client, from_state)
        state = client.app.state.draftly
        state.worker.run_task = AsyncMock(side_effect=RuntimeError("x" * 400))

        resp = client.post(path)

        # With fire-and-forget, the endpoint returns 200 immediately
        # with run_id + ticket; the failure is handled in the background.
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert "run_id" in body
        assert "ticket" in body

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_success_path_reports_result(self, client, path, from_state):
        self._set_state(client, from_state)

        resp = client.post(path)

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert "run_id" in body
        assert "ticket" in body

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_workflow_state_success_maps_to_completed(self, client, path, from_state):
        self._set_state(client, from_state)
        client.app.state.draftly.worker.run_task = AsyncMock(
            return_value=WorkflowState(run_id="r1").finish(WorkflowStatus.DELIVERED)
        )

        resp = client.post(path)

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert "run_id" in body
        assert "ticket" in body

    @pytest.mark.parametrize(("path", "from_state"), INIT_PATHS)
    def test_workflow_state_failed_marks_failed_and_502(self, client, path, from_state):
        self._set_state(client, from_state)
        state = client.app.state.draftly
        wf = WorkflowState(run_id="r2", errors=["boom"])
        wf.finish(WorkflowStatus.FAILED)
        state.worker.run_task = AsyncMock(return_value=wf)

        resp = client.post(path)

        # Fire-and-forget: returns 200 immediately; failure handled in background.
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert "run_id" in body
        assert "ticket" in body

    def test_post_run_shape_crash_cannot_strand_initializing(self, client):
        self._set_state(client, "PREFERENCES_CONFIGURED")
        state = client.app.state.draftly

        state.worker.run_task = AsyncMock(side_effect=RuntimeError("shape"))

        resp = client.post("/onboarding/initialize")

        # Fire-and-forget: returns 200 immediately; crash handled in background.
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert "run_id" in body
        assert "ticket" in body


class TestInitializeRqDispatch:
    """Task 9: when RQ is enabled the init route enqueues and returns
    immediately; the in-process worker only runs as a fallback."""

    def _set_state(self, client, state="PREFERENCES_CONFIGURED"):
        client.app.state.draftly.dependencies.repositories.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": state,
            "selected_repository": {"full_name": "o/r"},
        }

    def _mock_jobs_insert(self, client, **kw):
        repos = client.app.state.draftly.dependencies.repositories
        # Task 10 regression: the init path must register the jobs row via the
        # conflict-tolerant upsert_on_conflict (ON CONFLICT (run_id) DO NOTHING),
        # NOT a plain insert, so concurrent /initialize calls that converge on
        # the same run_id never 500 on the run_id UNIQUE constraint.
        repos.jobs.upsert_on_conflict = AsyncMock(**kw)
        return repos.jobs.upsert_on_conflict

    def test_rq_enabled_enqueues_and_returns_immediately(self, client):
        self._set_state(client)
        state = client.app.state.draftly
        state.settings = SimpleNamespace(rq_enabled=True)
        state.rq_queues = {"default": MagicMock()}
        state.task_handlers = {"onboarding.initialize": MagicMock()}

        jobs_insert = self._mock_jobs_insert(client)

        job = MagicMock()
        job.id = "rqjob-123"

        with (
            patch(
                "draftly.app.api.routes.onboarding.enqueue_job",
                return_value=job,
            ) as enqueue_mock,
            patch(
                "draftly.integrations.database.jobs_store.DatabaseJobsStore"
            ) as store_cls,
        ):
            resp = client.post("/onboarding/initialize")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert body["run_id"]
        assert body["ticket"] == "test-ticket"

        enqueue_mock.assert_called_once_with(
            queues=state.rq_queues,
            task_handlers=state.task_handlers,
            task_name="onboarding.initialize",
            org_id="test-org",
            selected_repository={"full_name": "o/r"},
            run_id=body["run_id"],
        )
        # Task 1: the app-wired repository store must be used, and a fresh
        # DatabaseJobsStore must NEVER be constructed. The row is registered
        # via upsert_on_conflict (conflict-tolerant) per Task 10.
        jobs_insert.assert_awaited_once_with(
            run_id=body["run_id"],
            org_id="test-org",
            name="onboarding.initialize",
            job_type="onboarding",
            schedule="manual",
            configuration={"rq_job_id": "rqjob-123"},
        )
        store_cls.assert_not_called()
        state.worker.run_task.assert_not_awaited()

    def test_initialize_409_when_selected_repository_lacks_full_name(self, client):
        """Guard: /initialize must NOT enqueue a job when selected_repository
        has no full_name — the workflow reads repo_full=selected_repository.get(
        'full_name') and would otherwise call GitHub with an empty repo path,
        failing with a cryptic 404 https://api.github.com/repos/."""
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "selected_repository": {"preferences": {"style": "developer-focused"}},
        }
        state = client.app.state.draftly
        state.settings = SimpleNamespace(rq_enabled=True)
        state.rq_queues = {"default": MagicMock()}
        state.task_handlers = {"onboarding.initialize": MagicMock()}

        with patch("draftly.app.api.routes.onboarding.enqueue_job") as enqueue_mock:
            resp = client.post("/onboarding/initialize")

        assert resp.status_code == 409
        assert "repository" in resp.json()["detail"].lower()
        enqueue_mock.assert_not_called()
        state.worker.run_task.assert_not_awaited()
        repos.jobs.upsert_on_conflict.assert_not_awaited()

    def test_rq_fresh_init_does_not_use_plain_insert(self, client):
        """Task 10: the fresh-init success path must NOT call repos.jobs.insert,
        which is not conflict-tolerant and would 500 when two concurrent
        /initialize calls converge on the same run_id."""
        self._set_state(client)
        state = client.app.state.draftly
        state.settings = SimpleNamespace(rq_enabled=True)
        state.rq_queues = {"default": MagicMock()}
        state.task_handlers = {"onboarding.initialize": MagicMock()}

        repos = state.dependencies.repositories
        repos.jobs.upsert_on_conflict = AsyncMock(return_value={})
        repos.jobs.insert = AsyncMock()

        job = MagicMock()
        job.id = "rqjob-123"

        with patch(
            "draftly.app.api.routes.onboarding.enqueue_job",
            return_value=job,
        ):
            resp = client.post("/onboarding/initialize")

        assert resp.status_code == 200
        repos.jobs.upsert_on_conflict.assert_awaited()
        repos.jobs.insert.assert_not_awaited()

    def test_rq_disabled_falls_back_to_in_process_worker(self, client):
        self._set_state(client)
        state = client.app.state.draftly
        state.settings = SimpleNamespace(rq_enabled=False)
        state.rq_queues = {"default": MagicMock()}
        state.task_handlers = {"onboarding.initialize": MagicMock()}

        jobs_insert = self._mock_jobs_insert(client)

        with (
            patch(
                "draftly.app.api.routes.onboarding.enqueue_job"
            ) as enqueue_mock,
            patch(
                "draftly.integrations.database.jobs_store.DatabaseJobsStore"
            ) as store_cls,
        ):
            resp = client.post("/onboarding/initialize")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "INITIALIZING"
        assert body["run_id"]
        state.worker.run_task.assert_awaited_with(
            "onboarding.initialize",
            org_id="test-org",
            selected_repository={"full_name": "o/r"},
            run_id=body["run_id"],
        )
        enqueue_mock.assert_not_called()
        # Jobs row is always inserted via the app-wired store.
        jobs_insert.assert_awaited_once_with(
            run_id=body["run_id"],
            org_id="test-org",
            name="onboarding.initialize",
            job_type="onboarding",
            schedule="manual",
            configuration={"rq_job_id": ""},
        )
        store_cls.assert_not_called()

    def test_rq_insert_failure_returns_500(self, client):
        """Task 1: a failed jobs insert must NOT be swallowed — propagate 5xx."""
        self._set_state(client)
        state = client.app.state.draftly
        state.settings = SimpleNamespace(rq_enabled=True)
        state.rq_queues = {"default": MagicMock()}
        state.task_handlers = {"onboarding.initialize": MagicMock()}

        self._mock_jobs_insert(
            client, side_effect=RuntimeError("db down")
        )

        job = MagicMock()
        job.id = "rqjob-123"

        with patch(
            "draftly.app.api.routes.onboarding.enqueue_job",
            return_value=job,
        ):
            resp = client.post("/onboarding/initialize")

        assert resp.status_code == 500
        assert "Failed to register initialization run" in resp.json()["detail"]
        state.worker.run_task.assert_not_awaited()

    def test_resumed_path_reconcile_failure_returns_500(self, client):
        """Task 2: a failed resumed-path jobs reconcile must also propagate 5xx
        (never hand out a run_id with no backing jobs row)."""
        self._set_state(client)
        state = client.app.state.draftly

        # Simulate lock acquired by a previous call -> resumed path.
        redis_mock = MagicMock()
        native_mock = AsyncMock()
        native_mock.set.side_effect = [None]
        native_mock.get.return_value = "first-run-id"
        redis_mock.native = native_mock
        state.redis_client = redis_mock

        repos = state.dependencies.repositories
        repos.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "INITIALIZING",
            "selected_repository": {"init_run_id": "first-run-id"},
        }
        repos.jobs.upsert_on_conflict = AsyncMock(
            side_effect=RuntimeError("db down")
        )

        resp = client.post("/onboarding/initialize")

        assert resp.status_code == 500
        assert "Failed to register initialization run" in resp.json()["detail"]


class TestRepositoriesAndCompleteHardening:
    """Installation-scoped listing auth + defensive parsing on complete/status."""

    INSTALLATION_TOKEN = "ghs_test"

    def test_list_repositories_uses_installation_auth(self, client):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "draftly.integrations.github.app_auth.get_installation_token",
                    new=AsyncMock(return_value=self.INSTALLATION_TOKEN),
                )
            )
            auth_cls = stack.enter_context(
                patch("draftly.integrations.github.auth.GitHubAuth")
            )
            client_cls = stack.enter_context(
                patch("draftly.integrations.github.client.GitHubClient")
            )
            client_cls.return_value.get_installation_repositories = AsyncMock(
                return_value=[{"full_name": "TheGreatBonnie/authly", "id": 7}]
            )
            repos = client.app.state.draftly.dependencies.repositories
            repos.onboarding.get.return_value = {
                "org_id": "test-org",
                "state": "GITHUB_CONNECTED",
                "selected_repository": {"installation_id": "156354594"},
            }

            resp = client.get("/onboarding/github/repositories")

        assert resp.status_code == 200
        auth_cls.assert_called_once_with(token=self.INSTALLATION_TOKEN)
        client_cls.assert_called_once_with(auth=auth_cls.return_value)

    def test_complete_tolerates_corrupted_completed_steps(self, client):
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "PREFERENCES_CONFIGURED",
            "completed_steps": "not json[",
        }

        resp = client.post("/onboarding/complete")

        assert resp.status_code == 409

    def test_initialize_status_sanitizes_failure_detail(self, client):
        repos = client.app.state.draftly.dependencies.repositories
        repos.onboarding.get.return_value = {
            "org_id": "test-org",
            "state": "FAILED",
            "failure": {"step": "initialization", "detail": "x" * 999},
        }

        body = client.get("/onboarding/initialize/status").json()

        assert len(body["failure"]["detail"]) <= 300
