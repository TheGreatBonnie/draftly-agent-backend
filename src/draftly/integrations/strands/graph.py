"""Per-run graph construction with session persistence (plan §6.8).

One run = one session = one graph. Session state is keyed by session_id;
sharing a single ``FileSessionManager`` across graphs makes them clobber
each other's persistence, so every run gets its own manager instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from strands.session import FileSessionManager, RepositorySessionManager

from draftly.integrations.strands.models import (
    resolve_model_for_role,  # noqa: F401 - re-exported for graph builders
)
from draftly.orchestration.graphs.content_graph import build_content_graph
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
    # Slack/Discord are support sources; the eval harness passes the source
    # surface directly, so resolve them here instead of falling through to the
    # documentation graph (which would author changelog entries for support
    # cases). Every other surface — including release — still defaults to the
    # documentation graph below.
    "slack": build_support_graph,
    "discord": build_support_graph,
    "content": build_content_graph,
}


def build_session_manager(
    run_id: str,
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR,
    repository: Any | None = None,
) -> Any:
    """Create a session manager for a specific run.

    A DB-backed ``SessionRepository`` (shared across processes/restarts) is
    used when ``repository`` is provided; otherwise the on-disk
    ``FileSessionManager`` keeps offline runs and tests isolated.
    """
    session_id = f"draftly-{run_id}"
    if repository is not None:
        return RepositorySessionManager(
            session_id=session_id,
            session_repository=repository,
        )
    Path(storage_dir).mkdir(parents=True, exist_ok=True)
    return FileSessionManager(
        session_id=session_id,
        storage_dir=storage_dir,
    )


def build_graph_for_run(
    run_id: str,
    surface: str,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    agents: Any = None,
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR,
    session_manager: Any | None = None,
    session_repository: Any | None = None,
    audit_repo: Any = None,
    memory: Any = None,
    publisher: Any = None,
    jobs_repo: Any = None,
    grounding: str = "local",
    repo_dir: str | None = None,
    steering_runtime: Any = None,
    **graph_kwargs: Any,
):
    """Build the graph for ONE surface, with its own session manager.

    ``surface`` is one of ``pull_request`` | ``issue`` | ``support``
    (see ``draftly.orchestration.routing.classifiers.workflow_for_event``).
    ``steering_runtime`` is the run-scoped SteeringRuntime (Task 6); every
    agent constructor receives it along with a stable agent/node identity.
    """
    builder = _BUILDERS.get(surface, build_documentation_graph)
    manager = session_manager or build_session_manager(
        run_id, storage_dir, repository=session_repository
    )

    # The content repository is consumed exclusively by the content graph;
    # drop it for every other builder so an injected repo never leaks into an
    # incompatible builder signature through **graph_kwargs.
    if surface != "content":
        graph_kwargs.pop("content_repository", None)

    # The slack/discord surfaces already encode the support origin: scope the
    # support graph's delivery tools to the originating platform.
    if surface in ("slack", "discord"):
        graph_kwargs.setdefault("source", surface)

    # Grounding mode (local checkout vs GitHub API vs docs-only) is a
    # documentation-graph concern; other builders must not receive it.
    if surface == "pull_request":
        graph_kwargs["grounding"] = grounding
        graph_kwargs["repo_dir"] = repo_dir

    return builder(
        session_manager=manager,
        tools_registry=tools_registry,
        agents=agents,
        model=model,
        hooks=hooks,
        audit_repo=audit_repo,
        memory=memory,
        publisher=publisher,
        jobs_repo=jobs_repo,
        steering_runtime=steering_runtime,
        **graph_kwargs,
    )
