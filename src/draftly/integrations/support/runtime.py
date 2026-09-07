"""Org-scoped support runtime context.

Tools and clients read the active ``SupportRuntimeContext`` at call time to
resolve tenant credentials and reply targets. The context lives in a
``contextvar`` around graph invocation and review resume; it is never
serialized into the prompt, keeping platform credentials out of model input.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SupportRuntimeContext:
    """Resolved tenant target for a support delivery."""

    org_id: str
    platform: str
    platform_account_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None


_support_runtime: ContextVar[SupportRuntimeContext | None] = ContextVar(
    "draftly_support_runtime", default=None
)


def set_support_runtime(context: SupportRuntimeContext | None):
    """Set the active support runtime; returns the reset token."""
    return _support_runtime.set(context)


def reset_support_runtime(token) -> None:
    _support_runtime.reset(token)


def current_support_runtime() -> SupportRuntimeContext | None:
    return _support_runtime.get()


def _as_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def support_runtime_for(event: dict[str, Any]) -> SupportRuntimeContext | None:
    """Build a runtime context from a normalized support event, else ``None``."""
    platform = str(event.get("source") or "")
    if platform not in ("slack", "discord"):
        return None
    return SupportRuntimeContext(
        org_id=str(event.get("project_id") or event.get("org_id") or ""),
        platform=platform,
        platform_account_id=_as_str(event.get("team_id") or event.get("guild_id")),
        channel_id=_as_str(event.get("channel") or event.get("channel_id")),
        thread_id=_as_str(event.get("thread_ts")),
    )


def default_slack_installation_store():
    """Lazily build the DB-backed installation store used by support tools."""
    from draftly.app.dependencies import build_dependencies
    from draftly.integrations.slack.installation_store import SlackInstallationStore

    deps = build_dependencies()
    return SlackInstallationStore(deps.integrations.database)
