"""Interaction token store for Discord review actions."""
from __future__ import annotations

_token_map: dict[str, str] = {}


def store_interaction_token(short_key: str, full_token: str) -> None:
    """Store a mapping from a short key to a full review token."""
    _token_map[short_key] = full_token


def resolve_interaction_token(short_key: str) -> str | None:
    """Resolve a short key to a full review token. Returns None if not found."""
    return _token_map.get(short_key)
