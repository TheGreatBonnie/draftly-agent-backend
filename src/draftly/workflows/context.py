"""WorkflowContext: dependency bundle for workflow functions (plan §7.2).

Imported by ``app/composition/workers.py`` and the runner. Carries the
repositories, config, and Strands runtime handles a workflow needs —
never a FastAPI app object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    #: Concrete strands Model for graph agents (None ⇒ offline/test mode).
    model: Any = None
    hooks: list[Any] = field(default_factory=list)
    storage_dir: str = DEFAULT_SESSION_STORAGE_DIR
    audit_repo: Any = None
    #: RoutingDecision from the adaptive router when per-task routing ran.
    routing_decision: Any | None = None
    #: Agentic-memory subsystems (spec 2026-08-23); None disables them.
    episodic: Any = None
    procedural: Any = None
    docgraph: Any = None
    candidates: Any = None

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
        ):
            value = getattr(strands, attr, None)
            if value is not None:
                limits[attr] = value
        return limits
