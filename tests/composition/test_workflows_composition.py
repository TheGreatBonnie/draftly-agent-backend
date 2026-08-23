"""Composition wires RedisEventBus into the runner only when enabled."""

from __future__ import annotations

from typing import Any

import pytest

from draftly.app.composition.workflows import build_workflows


class StubSettings:
    worker_enabled = True
    events_streaming_enabled = False
    redis_url = "redis://localhost:6379/0"
    strands_review_policy = "always"
    strands_session_storage_dir = ".draftly/sessions"

    strands = type("S", (), {"session_storage_dir": ".draftly/sessions"})()

    def graph_limits(self) -> dict[str, Any]:
        return {}

    def review_policy(self) -> str:
        return "always"

    def __getattr__(self, name: str) -> Any:
        return None


@pytest.fixture(autouse=True)
def _fake_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # composition reads repositories from env; no live DB needed because
    # repository constructors only hold connection config at this stage
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/dummy")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://localhost:5432/dummy")


def _build(enabled: bool) -> Any:
    settings = StubSettings()
    settings.events_streaming_enabled = enabled
    return build_workflows(config=settings)


def test_disabled_flag_leaves_runner_unpublished() -> None:
    composed = _build(False)
    assert composed.runner.publisher is None


def test_enabled_flag_attaches_bus() -> None:
    composed = _build(True)
    assert composed.runner.publisher is not None
