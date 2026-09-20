"""Tavily configuration flags must reject enabled features without an API key.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (Configuration).
Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 1).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.app.config import Settings


def _settings(**overrides):
    """Build Settings with explicit Tavily kwargs (hermetic vs env/.env)."""
    base = {
        "tavily_api_key": None,
        "tavily_public_ingestion_enabled": False,
        "tavily_live_fallback_enabled": False,
        "tavily_research_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize(
    "flag",
    [
        "tavily_public_ingestion_enabled",
        "tavily_live_fallback_enabled",
        "tavily_research_enabled",
    ],
)
def test_enabled_flag_without_key_raises(flag: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{flag: True})


def test_no_flags_and_no_key_valid() -> None:
    settings = _settings()
    assert settings.tavily_enabled is False


def test_all_flags_off_with_key_valid() -> None:
    settings = _settings(tavily_api_key="tvly-test-key")
    assert settings.tavily_enabled is False


def test_all_flags_on_with_key_valid() -> None:
    settings = _settings(
        tavily_api_key="tvly-test-key",
        tavily_public_ingestion_enabled=True,
        tavily_live_fallback_enabled=True,
        tavily_research_enabled=True,
    )
    assert settings.tavily_enabled is True


def test_tavily_enabled_property_true_when_any_flag_on() -> None:
    settings = _settings(
        tavily_api_key="tvly-test-key",
        tavily_research_enabled=True,
    )
    assert settings.tavily_enabled is True


def test_negative_timeout_raises() -> None:
    with pytest.raises(ValidationError):
        _settings(tavily_request_timeout_seconds=0)


def test_negative_poll_timeout_raises() -> None:
    with pytest.raises(ValidationError):
        _settings(tavily_research_poll_timeout_seconds=-1)


def test_zero_concurrency_raises() -> None:
    with pytest.raises(ValidationError):
        _settings(tavily_max_concurrency=0)


def test_tavily_defaults_match_spec() -> None:
    settings = _settings()
    assert settings.tavily_base_url == "https://api.tavily.com"
    assert settings.tavily_request_timeout_seconds == 60
    assert settings.tavily_research_poll_timeout_seconds == 300
    assert settings.tavily_max_concurrency == 4
    assert settings.tavily_credit_budget is None
