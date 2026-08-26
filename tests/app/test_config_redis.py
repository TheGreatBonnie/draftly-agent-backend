"""Tests for Redis subsystem settings on the Settings model."""


def test_redis_settings_defaults():
    from draftly.app.config import Settings

    s = Settings(
        database_url="sqlite:///test.db",
        redis_url="redis://localhost:6379/0",
    )
    assert s.semantic_cache_enabled is True
    assert s.semantic_cache_similarity_threshold == 0.90
    assert s.vector_search_backend == "dual"
    assert s.event_bus_backend == "dual"
    assert s.rate_limiting_enabled is True
    assert s.api_cache_enabled is True


def test_redis_settings_can_be_overridden():
    from draftly.app.config import Settings

    s = Settings(
        database_url="sqlite:///test.db",
        redis_url="redis://localhost:6379/1",
        semantic_cache_enabled=False,
        semantic_cache_similarity_threshold=0.80,
        vector_search_backend="redis",
        event_bus_backend="pubsub",
        rate_limiting_enabled=False,
        api_cache_enabled=False,
    )
    assert s.semantic_cache_enabled is False
    assert s.semantic_cache_similarity_threshold == 0.80
    assert s.vector_search_backend == "redis"
    assert s.event_bus_backend == "pubsub"
    assert s.rate_limiting_enabled is False
    assert s.api_cache_enabled is False
