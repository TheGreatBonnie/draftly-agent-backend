"""Tool-scoping adapter between the app ToolRegistry and graph builders.

Graph builders consume a duck-typed registry exposing scoped tool-group
attributes (``github_intelligence``, ``slack_search``, ...). This module
gives non-app callers (workers, tests, scripts) a tiny facade so they don't
need to import the FastAPI composition layer.
"""

from __future__ import annotations

from typing import Any

from draftly.app.composition.tools import ToolRegistry, build_tools


class GraphTools:
    """Read-only view over :class:`ToolRegistry` scoped groups."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry if registry is not None else build_tools()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def research_channel_tools(self, channel: str) -> list[Any]:
        """Search + thread tools for one support channel."""
        prefix = f"{channel}_"
        return [
            tool
            for attr in (
                f"{prefix}search",
                f"{prefix}get_thread",
            )
            for tool in (getattr(self._registry, attr, None) or [])
        ]

    def delivery_tools(self) -> list[Any]:
        """Everything the deliver node may use to ship output."""
        reg = self._registry
        return [
            *reg.github_delivery,
            *reg.slack_post_message,
            *reg.discord_post_message,
        ]


def build_graph_tools() -> GraphTools:
    """Build the default graph-facing tools view."""
    return GraphTools()
