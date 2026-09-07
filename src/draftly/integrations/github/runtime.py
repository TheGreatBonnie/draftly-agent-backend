"""Per-workflow GitHub authentication context."""

from __future__ import annotations

from contextvars import ContextVar, Token

_installation_id: ContextVar[int | None] = ContextVar(
    "github_installation_id",
    default=None,
)


def set_installation_id(installation_id: int | None) -> Token[int | None]:
    """Set the installation used by GitHub tools in the current workflow."""
    return _installation_id.set(int(installation_id) if installation_id else None)


def reset_installation_id(token: Token[int | None]) -> None:
    """Restore the previous workflow authentication context."""
    _installation_id.reset(token)


def current_installation_id() -> int | None:
    return _installation_id.get()
