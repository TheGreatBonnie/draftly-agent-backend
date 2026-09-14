from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from draftly.app.agentcore.app import create_agentcore_app


def test_settings_expose_agentcore_port() -> None:
    from draftly.app.config import get_settings

    assert hasattr(get_settings(), "agentcore_port")


def test_ping_route_is_registered() -> None:
    app = create_agentcore_app(with_lifespan=False)

    assert app.url_path_for("ping") == "/ping"


def test_ping_returns_healthy() -> None:
    client = TestClient(create_agentcore_app(with_lifespan=False))

    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_prepare_workflows_composes_without_worker_boot() -> None:
    from draftly.app.lifecycle import DraftlyApplication

    app = DraftlyApplication(
        settings=MagicMock(),
        dependencies=MagicMock(),
        tools=object(),
    )
    start_infra = AsyncMock()
    build_agents = AsyncMock()

    with patch.object(DraftlyApplication, "_start_infrastructure", start_infra), \
         patch.object(DraftlyApplication, "_build_agents_and_workflows", build_agents):
        await app.prepare_workflows()

    assert app._started is True
    start_infra.assert_awaited_once()
    build_agents.assert_awaited_once()