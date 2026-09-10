"""WorkflowContext: dependency bundle for workflow functions (plan §7.2).

Imported by ``app/composition/workers.py`` and the runner. Carries the
repositories, config, and Strands runtime handles a workflow needs —
never a FastAPI app object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

DEFAULT_SESSION_STORAGE_DIR = ".draftly/sessions"


@dataclass
class WorkflowContext:
    """Everything a workflow function may touch, explicitly."""

    repositories: Any = None
    memory: Any = None
    evaluation: Any = None
    feedback: Any = None
    config: Any = None
    tools: Any = None
    #: Factory-only registry used to build fresh per-run graph agents.
    agents: Any = None
    #: Concrete strands Model for graph agents (None ⇒ offline/test mode).
    model: Any = None
    hooks: list[Any] = field(default_factory=list)
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR
    #: DB-backed Strands session repository shared across processes/restarts
    #: (resume-session fix); None ⇒ per-run FileSessionManager.
    session_repository: Any = None
    audit_repo: Any = None
    #: RoutingDecision from the adaptive router when per-task routing ran.
    routing_decision: Any | None = None
    #: Agentic-memory subsystems (spec 2026-08-23); None disables them.
    episodic: Any = None
    procedural: Any = None
    docgraph: Any = None
    candidates: Any = None
    #: Streaming publisher (spec 2026-08-23-event-streaming); None ⇒ flag off.
    #: Set by composition so per-surface workflow functions can stream too.
    publisher: Any = None
    #: Org-scoped dashboard broadcaster (SSE push); None disables pushes.
    #: Set by composition when Redis + streaming are enabled.
    broadcaster: Any = None
    #: The composed runner, populated after workflow construction for
    #: event-driven handoffs such as feedback-gap content generation.
    runner: Any = None
    #: Org-scoped reviewer notifier (best-effort Slack/Discord on pending
    #: review); None ⇒ notification dispatching disabled.
    notifier: Any = None

    @property
    def events(self) -> Any:
        return getattr(self.repositories, "events", None)

    @property
    def reviews(self) -> Any:
        return getattr(self.repositories, "reviews", None)

    def review_policy(self) -> str:
        """Review policy from config; 'always' when unconfigured."""
        strands = getattr(self.config, "strands", None)
        policy = getattr(strands, "review_policy", None)
        return policy if isinstance(policy, str) and policy else "always"

    def memory_bundle(self) -> Any:
        """Expose org-scoped knowledge, episode, and procedure sources to graphs."""
        if self.memory is None:
            return None

        async def knowledge(query: str, *, limit: int = 5, org_id: str | None = None):
            return await self.memory.recall_knowledge(query, limit=limit, org_id=org_id)

        async def episodes(query: str, *, limit: int = 2, org_id: str | None = None):
            if self.episodic is None:
                return []
            return await self.episodic.find_similar(query, org_id=org_id, limit=limit)

        async def procedures(query: str, *, limit: int = 1, org_id: str | None = None):
            if self.procedural is None:
                return []
            return await self.procedural.match(query, org_id=org_id, limit=limit)

        return SimpleNamespace(
            knowledge=knowledge,
            episodes=episodes,
            procedures=procedures,
        )

    def graph_limits(self) -> dict[str, Any]:
        """Strands graph budget knobs from config (forwarded to builder)."""
        strands = getattr(self.config, "strands", None)
        if strands is None:
            return {}
        limits: dict[str, Any] = {}
        for attr in (
            "max_node_executions",
            "execution_timeout",
            "node_timeout",
            "evaluator_max_iterations",
        ):
            value = getattr(strands, attr, None)
            if value is not None:
                limits[attr] = value
        return limits
