"""Strands runtime facade.

Ties together model resolution, tool scoping, and per-run graph
construction behind one object the composition layer (Phase 5) hands to
the workflow runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from draftly.integrations.strands.graph import (
    DEFAULT_SESSION_STORAGE_DIR,
    build_graph_for_run,
    build_session_manager,
)
from draftly.integrations.strands.models import RoleAwareModelResolver, build_model
from draftly.integrations.strands.tools import GraphTools, build_graph_tools
from draftly.models.router import ModelRouter

logger = structlog.get_logger(__name__)


@dataclass
class StrandsClient:
    """Single entry point for building and invoking Draftly graphs."""

    tools: GraphTools = field(default_factory=build_graph_tools)
    model: Any = None
    session_storage_dir: str = DEFAULT_SESSION_STORAGE_DIR
    audit_repo: Any = None
    hooks: list[Any] = field(default_factory=list)
    content_repository: Any = None

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = build_model()

        if isinstance(self.model, ModelRouter):
            self.model = RoleAwareModelResolver(self.model)

    def graph_for_run(
        self,
        run_id: str,
        surface: str,
        **graph_kwargs: Any,
    ):
        """Build a fresh per-run graph with its own session manager."""
        # The content graph is the only consumer of the content repository;
        # other builders do not accept it and inject it selectively so a
        # non-content run never leaks the kwarg into an incompatible builder.
        if surface == "content" and self.content_repository is not None:
            graph_kwargs["content_repository"] = self.content_repository
        return build_graph_for_run(
            run_id,
            surface=surface,
            tools_registry=self.tools,
            model=self.model,
            hooks=self.hooks,
            storage_dir=self.session_storage_dir,
            audit_repo=self.audit_repo,
            **graph_kwargs,
        )

    def session_manager(self, run_id: str):
        return build_session_manager(run_id, self.session_storage_dir)

    async def invoke(
        self,
        run_id: str,
        surface: str,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **graph_kwargs: Any,
    ):
        """Build + invoke a graph in one call (convenience for tests/tools)."""
        graph = self.graph_for_run(run_id, surface, **graph_kwargs)
        return await graph.invoke_async(task, invocation_state or {})
