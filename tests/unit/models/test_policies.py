"""Tests for routing policies."""

from draftly.models.policies import FALLBACKS, KNOWN_PROVIDERS


def test_known_providers_includes_bedrock_and_mantle():
    assert "bedrock" in KNOWN_PROVIDERS
    assert "mantle" in KNOWN_PROVIDERS


def test_fallbacks_chains_include_bedrock_and_mantle():
    for chain_key, chain in FALLBACKS.items():
        assert "bedrock" in chain, f"bedrock missing from {chain_key} chain"
        assert "mantle" in chain, f"mantle missing from {chain_key} chain"
