"""Tests for routing policies."""

import pytest

from draftly.models.policies import (
    AgentModelPolicy,
    FALLBACKS,
    KNOWN_PROVIDERS,
)


def test_known_providers_includes_bedrock_and_mantle():
    assert "bedrock" in KNOWN_PROVIDERS
    assert "mantle" in KNOWN_PROVIDERS


def test_fallbacks_chains_include_bedrock_and_mantle():
    for chain_key, chain in FALLBACKS.items():
        assert "bedrock" in chain, f"bedrock missing from {chain_key} chain"
        assert "mantle" in chain, f"mantle missing from {chain_key} chain"


def test_agent_policy_has_no_output_token_cap():
    policy = AgentModelPolicy(
        role="documentation_engineer",
        primary=("fast-requesty-luna",),
        fallback=("fast-orca-luna",),
        capabilities=("tool_calling", "structured_output"),
    )

    assert not hasattr(policy, "max_output_tokens")
    with pytest.raises(AttributeError):
        _ = policy.max_output_tokens
