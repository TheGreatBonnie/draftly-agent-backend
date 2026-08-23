"""Cost estimation from ModelConfig pricing metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from draftly.models.config import ModelConfig

#: Conservative stand-in cost (USD for a typical request) applied to models
#: with no declared pricing, per spec "unpriced models get a conservative
#: sentinel cost". High enough that budget filters prefer priced peers.
UNPRICED_MODEL_COST = 0.50


def estimate_cost(
    model: ModelConfig,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    """Estimate USD cost for a request against this model."""
    if (
        model.input_cost_per_1m_tokens is None
        and model.output_cost_per_1m_tokens is None
    ):
        return UNPRICED_MODEL_COST

    input_rate = model.input_cost_per_1m_tokens or 0.0
    output_rate = model.output_cost_per_1m_tokens or 0.0
    return (input_rate * input_tokens + output_rate * output_tokens) / 1_000_000
