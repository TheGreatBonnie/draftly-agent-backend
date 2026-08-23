"""Vector helpers shared by the agentic-memory stores."""

from __future__ import annotations

from collections.abc import Sequence

EMBEDDING_DIMENSIONS = 1536


def normalize_vector(
    embedding: Sequence[float],
    dim: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Truncate or zero-pad to the column dimension so writes never fail."""
    values = [float(v) for v in embedding][:dim]
    values.extend([0.0] * (dim - len(values)))
    return values


def format_vector(embedding: Sequence[float]) -> str:
    """Format for pgvector text input: '[a,b,c]'."""
    return "[" + ",".join(str(float(v)) for v in embedding) + "]"
