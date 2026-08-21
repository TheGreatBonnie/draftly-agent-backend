"""Per-run graph construction with session persistence (plan §6.8).

One run = one session = one graph. Session state is keyed by session_id;
sharing a single ``FileSessionManager`` across graphs makes them clobber
each other's persistence, so every run gets its own manager instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from strands.session import FileSessionManager

from draftly.orchestration.graphs.documentation_graph import (
    build_documentation_graph,
)
from draftly.orchestration.graphs.issue_graph import build_issue_graph
from draftly.orchestration.graphs.support_graph import build_support_graph

DEFAULT_SESSION_STORAGE_DIR = ".draftly/sessions"

_BUILDERS = {
    "pull_request": build_documentation_graph,
    "issue": build_issue_graph,
    "support": build_support_graph,
}


def build_session_manager(
    run_id: str,
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR,
) -> FileSessionManager:
    """Create a FileSessionManager for a specific run."""
    Path(storage_dir).mkdir(parents=True, exist_ok=True)
    return FileSessionManager(
        session_id=f"draftly-{run_id}",
        storage_dir=storage_dir,
    )


def build_graph_for_run(
    run_id: str,
    surface: str,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR,
    session_manager: Any | None = None,
    audit_repo: Any = None,
    memory: Any = None,
    **graph_kwargs: Any,
):
    """Build the graph for ONE surface, with its own session manager.

    ``surface`` is one of ``pull_request`` | ``issue`` | ``support``
    (see ``draftly.orchestration.routing.classifiers.workflow_for_event``).
    """
    builder = _BUILDERS.get(surface, build_documentation_graph)
    manager = session_manager or build_session_manager(run_id, storage_dir)

    return builder(
        session_manager=manager,
        tools_registry=tools_registry,
        model=model,
        hooks=hooks,
        audit_repo=audit_repo,
        memory=memory,
        **graph_kwargs,
    )
