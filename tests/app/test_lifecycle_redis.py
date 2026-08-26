"""Tests for RedisClient wiring into the application lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_create_application_creates_redis_client():
    from draftly.app.config import Settings
    from draftly.app.lifecycle import create_application

    settings = Settings(
        database_url="sqlite:///test.db",
        redis_url="redis://localhost:6379/0",
    )
    with (
        patch("draftly.integrations.redis.RedisClient") as MockClient,
        patch("draftly.app.lifecycle.build_dependencies") as mock_build_deps,
    ):
        MockClient.return_value = MagicMock()
        mock_build_deps.return_value = MagicMock()
        app = create_application(settings=settings)
        assert app.redis_client is not None
        MockClient.assert_called_once_with(url="redis://localhost:6379/0")


def test_draftly_application_has_redis_client_field():
    from draftly.app.lifecycle import DraftlyApplication

    app = DraftlyApplication(
        settings=MagicMock(),
        dependencies=MagicMock(),
        tools=MagicMock(),
    )
    assert app.redis_client is None


def test_lifecycle_shutdown_closes_redis_client():
    from draftly.app.lifecycle import DraftlyApplication

    app = DraftlyApplication(
        settings=MagicMock(),
        dependencies=MagicMock(),
        tools=MagicMock(),
    )
    redis_client = AsyncMock()
    app.redis_client = redis_client
    app._started = True

    import asyncio

    asyncio.run(app.shutdown())
    redis_client.close.assert_awaited_once()


def test_lifecycle_shutdown_noop_when_no_redis_client():
    from draftly.app.lifecycle import DraftlyApplication

    app = DraftlyApplication(
        settings=MagicMock(),
        dependencies=MagicMock(),
        tools=MagicMock(),
    )
    app._started = True
    app.redis_client = None

    import asyncio

    asyncio.run(app.shutdown())  # should not raise


def test_dependency_injection_get_redis_client():
    from draftly.app.dependencies import get_redis_client

    state = MagicMock()
    state.draftly.redis_client = "fake-client"
    assert get_redis_client(state) == "fake-client"


def test_dependency_injection_get_redis_client_none_when_no_draftly():
    from draftly.app.dependencies import get_redis_client

    state = MagicMock(spec=[])  # no draftly attribute
    assert get_redis_client(state) is None


def test_dependency_injection_get_ticket_store():
    from draftly.app.dependencies import get_ticket_store

    state = MagicMock()
    mock_client = MagicMock()
    mock_client.native = MagicMock()
    state.draftly.redis_client = mock_client

    with patch("draftly.integrations.ticket_store.RedisTicketStore") as MockStore:
        MockStore.return_value = "fake-store"
        result = get_ticket_store(state)
        assert result == "fake-store"
        MockStore.assert_called_once_with(mock_client.native)


def test_dependency_injection_get_ticket_store_none_when_no_redis():
    from draftly.app.dependencies import get_ticket_store

    state = MagicMock()
    state.draftly.redis_client = None
    assert get_ticket_store(state) is None


def test_dependency_injection_get_ema_stats_store():
    from draftly.app.dependencies import get_ema_stats_store

    state = MagicMock()
    mock_client = MagicMock()
    mock_client.native = MagicMock()
    state.draftly.redis_client = mock_client

    with patch("draftly.models.redis_performance.RedisEMAStatsStore") as MockStore:
        MockStore.return_value = "fake-ema-store"
        result = get_ema_stats_store(state)
        assert result == "fake-ema-store"
        MockStore.assert_called_once_with(mock_client.native)


def test_dependency_injection_get_ema_stats_store_none_when_no_redis():
    from draftly.app.dependencies import get_ema_stats_store

    state = MagicMock()
    state.draftly.redis_client = None
    assert get_ema_stats_store(state) is None


def test_dependency_injection_get_provider_health():
    from draftly.app.dependencies import get_provider_health

    state = MagicMock()
    mock_client = MagicMock()
    mock_client.native = MagicMock()
    state.draftly.redis_client = mock_client

    with patch("draftly.models.redis_health.RedisProviderHealth") as MockHealth:
        MockHealth.return_value = "fake-health"
        result = get_provider_health(state)
        assert result == "fake-health"
        MockHealth.assert_called_once_with(mock_client.native)


def test_dependency_injection_get_provider_health_none_when_no_redis():
    from draftly.app.dependencies import get_provider_health

    state = MagicMock()
    state.draftly.redis_client = None
    assert get_provider_health(state) is None


def test_build_workflows_accepts_redis_client():
    from draftly.app.composition.workflows import build_workflows

    with patch.dict("os.environ", {"DATABASE_URL": "sqlite:///test.db"}):
        result = build_workflows(
            config=MagicMock(events_streaming_enabled=False),
            redis_client=MagicMock(),
        )
        assert result.event_bus is None


def test_build_workflows_event_bus_uses_shared_redis_client():
    from draftly.app.composition.workflows import build_workflows

    mock_config = MagicMock()
    mock_config.events_streaming_enabled = True
    mock_config.redis_url = "redis://localhost:6379/0"

    mock_redis = MagicMock()
    mock_redis.native = MagicMock()

    with (
        patch.dict("os.environ", {"DATABASE_URL": "sqlite:///test.db"}),
        patch("draftly.events.redis_bus.RedisEventBus") as MockBus,
    ):
        MockBus.return_value = MagicMock()
        build_workflows(
            config=mock_config,
            redis_client=mock_redis,
        )
        MockBus.assert_called_once_with(
            redis_client=mock_redis.native,
            url=None,
        )
