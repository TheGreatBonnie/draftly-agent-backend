"""Tests for agent steering configuration fields in StrandsConfig/Settings."""

from draftly.app.config import Settings, StrandsConfig


def test_steering_config_defaults():
    config = StrandsConfig()
    assert config.steering_enabled is False
    assert config.steering_enforcement_enabled is False
    assert config.steering_policy_version == "v1"
    assert config.steering_llm_enabled is False
    assert config.steering_tool_guides_per_call == 2
    assert config.steering_model_guides_per_turn == 2
    assert config.steering_total_guides_per_agent == 5
    assert config.steering_judge_timeout_seconds == 10.0
    assert config.steering_reason_max_chars == 1_000
    assert config.steering_payload_max_bytes == 4 * 1024


def test_settings_strands_property_exposes_steering_fields():
    settings = Settings()
    config = settings.strands
    assert isinstance(config, StrandsConfig)
    assert config.steering_policy_version == "v1"
    assert config.steering_tool_guides_per_call == 2


def test_settings_load_steering_env_overrides(monkeypatch):
    monkeypatch.setenv("STRANDS_STEERING_ENABLED", "true")
    monkeypatch.setenv("STRANDS_STEERING_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("STRANDS_STEERING_LLM_ENABLED", "true")
    monkeypatch.setenv("STRANDS_STEERING_TOOL_GUIDES_PER_CALL", "5")
    monkeypatch.setenv("STRANDS_STEERING_MODEL_GUIDES_PER_TURN", "3")
    monkeypatch.setenv("STRANDS_STEERING_TOTAL_GUIDES_PER_AGENT", "7")
    monkeypatch.setenv("STRANDS_STEERING_JUDGE_TIMEOUT_SECONDS", "30.0")
    monkeypatch.setenv("STRANDS_STEERING_PAYLOAD_MAX_BYTES", "8192")
    settings = Settings()
    config = settings.strands
    assert config.steering_enabled is True
    assert config.steering_enforcement_enabled is True
    assert config.steering_llm_enabled is True
    assert config.steering_tool_guides_per_call == 5
    assert config.steering_model_guides_per_turn == 3
    assert config.steering_total_guides_per_agent == 7
    assert config.steering_judge_timeout_seconds == 30.0
    assert config.steering_payload_max_bytes == 8192


def test_existing_review_policy_unaffected_by_steering_defaults():
    settings = Settings(strands_review_policy="risky")
    assert settings.strands.review_policy == "risky"
    assert settings.strands.steering_enabled is False