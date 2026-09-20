"""Documentation research swarm: grounding-aware evidence collection.

The swarm follows the run's grounding: ``local`` spawns a local-repo
researcher (default — the offline evaluation harness has a local authly
worktree and no usable GitHub API), ``github`` spawns a GitHub researcher
scoped to the read-only GitHub API tools for real linked PRs, and ``docs``
uses only the documentation researcher.

When ``capability_aware_research`` is on, ``build_doc_research_swarm``
receives a ``ResearchPlan`` (spec: worker-latency Task 6) and constructs
**only** the planned researchers with the plan's budget limits. A failed
plan (missing mandatory capability) returns a deterministic gate node so
the graph fails fast without invoking impact.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands import Agent
from strands.multiagent import Swarm
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import load_skills, local_repo_note_for
from draftly.agents.shared.research import (
    build_docs_researcher,
    build_github_researcher,
)
from draftly.orchestration.nodes.base import agent_result
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from draftly.workflows.grounding import DOCS, GITHUB

logger = structlog.get_logger(__name__)


def _local_researcher_prompt(repo_dir: str | None) -> str:
    """System prompt for the local-repo researcher.

    Anchors the researcher to the concrete ``repo_dir`` when one is known so
    repo tools never silently operate on the wrong checkout (the container
    cwd). Keeps the GitHub-API prohibition hard-coded — offline harness runs
    have no usable API regardless of the checkout.
    """
    note = local_repo_note_for(repo_dir)
    if note:
        return (
            "You research the LOCAL repository checkout for evidence relevant "
            "to the event. The PR/diff/changed files are already provided in "
            "the task context; confirm them with the local repository tools "
            "(code_search, read_file, list_directory, git_*) by ALWAYS passing "
            f"repo_dir=<{repo_dir}>. Do NOT call GitHub web tools "
            "(get_pull_request, get_files, get_diff); never operate on files "
            "outside that checkout root. Collect concrete source ids (file "
            "paths + line numbers)."
        )
    return (
        "You research the LOCAL repository checkout for evidence relevant to "
        "the event. The PR/diff/changed files are already provided in the task "
        "context; confirm them with the local repository tools (code_search, "
        "read_file, list_directory, git_*). When a checkout path is known it "
        "is passed as repo_dir=<local checkout path> to the repo tools. Do NOT "
        "call GitHub web tools (get_pull_request, get_files, get_diff): the "
        "network API is unavailable. Collect concrete source ids (file paths "
        "+ line numbers)."
    )


def _local_researcher(
    model: Any,
    local_tools: list[Any],
    repo_dir: str | None,
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
) -> Agent:
    return build_draftly_agent(
        role=AgentRole.RESEARCH,
        system_prompt=_local_researcher_prompt(repo_dir),
        model=model,
        tools=local_tools,
        plugins=[AgentSkills(skills=load_skills("github-pr-analysis", "github-release-analysis"))],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "local_repo_researcher",
        node_id=node_id or "doc_research",
        name="local_repo_researcher",
        description="Researches local repository evidence for the event.",
    )


def _swarm_for_plan(
    model: Any,
    tools: Any,
    *,
    plan: Any,
    local_tools: list[Any],
    repo_dir: str | None,
    github_tools: list[Any],
    runtime: SteeringRuntime | None,
    node_id: str | None,
    execution_timeout: float | None = None,
    node_timeout: float | None = None,
) -> Swarm:
    """Construct ONLY the planned researchers with the plan's budget limits.

    ``plan.failure`` (missing mandatory capability) is handled by the caller;
    here every planned researcher is a capability the run has evidence for.
    Explicit ``execution_timeout``/``node_timeout`` overrides win over the
    plan's lowered defaults so the operator-configured Strands budgets
    (``STRANDS_NODE_TIMEOUT`` etc.) reach the research swarm.
    """
    agents: list[Agent] = []
    for name in plan.researchers:
        if name == "local":
            agents.append(
                _local_researcher(
                    model, local_tools, repo_dir, runtime=runtime, node_id=node_id or "doc_research"
                )
            )
        elif name == "github":
            agents.append(
                build_github_researcher(
                    model, github_tools or [], runtime=runtime, node_id=node_id or "doc_research"
                )
            )
        elif name == "docs":
            agents.append(
                build_docs_researcher(
                    model,
                    [tools.semantic_search, tools.keyword_search, tools.hybrid_search],
                    runtime=runtime,
                    node_id=node_id or "doc_research",
                )
            )
        else:
            raise ValueError(f"unknown planned researcher {name!r}")
    if not agents:
        raise ValueError("research plan selected no researchers")
    return Swarm(
        agents,
        entry_point=agents[0],
        max_handoffs=plan.max_handoffs,
        max_iterations=plan.max_iterations,
        execution_timeout=(
            execution_timeout if execution_timeout is not None else plan.execution_timeout
        ),
        node_timeout=node_timeout if node_timeout is not None else plan.node_timeout,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )


class ResearchCapabilityGate(MultiAgentBase):
    """Deterministic research node for a failed capability plan.

    Returns a FAILED graph result naming the missing mandatory capability so
    the run fails fast and impact/answer/generation are never invoked with
    evidence the run cannot gather.
    """

    def __init__(self, *, name: str = "research", failure: str) -> None:
        self.name = name
        self.failure = failure

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        run_id = (invocation_state or {}).get("run_id")
        logger.warning(
            "research_capability_gate",
            run_id=run_id,
            failure=self.failure,
        )
        return MultiAgentResult(
            status=Status.FAILED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "failure": self.failure,
                            "documented": False,
                        }
                    )
                )
            },
        )


def build_doc_research_swarm(
    model: Any,
    tools: Any,
    local_tools: list[Any] | None = None,
    *,
    repo_dir: str | None = None,
    github_tools: list[Any] | None = None,
    grounding: str = "local",
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
    plan: Any | None = None,
    execution_timeout: float | None = None,
    node_timeout: float | None = None,
) -> MultiAgentBase:
    """Build the research swarm used by the documentation graph.

    With a ``ResearchPlan`` (capability-aware lane), construct only the planned
    researchers; a plan with ``failure`` yields a deterministic
    ``ResearchCapabilityGate`` instead of a swarm.

    ``execution_timeout``/``node_timeout`` bound the swarm; when omitted the
    legacy hardcoded budgets (1800s/600s) or the plan's own budgets apply.
    """
    if plan is not None and plan.failure:
        return ResearchCapabilityGate(
            name=node_id or "research", failure=plan.failure
        )
    if plan is not None:
        return _swarm_for_plan(
            model,
            tools,
            plan=plan,
            local_tools=local_tools or [],
            repo_dir=repo_dir,
            github_tools=github_tools or [],
            runtime=runtime,
            node_id=node_id or "doc_research",
            execution_timeout=execution_timeout,
            node_timeout=node_timeout,
        )

    docs_agent = build_docs_researcher(
        model,
        [tools.semantic_search, tools.keyword_search, tools.hybrid_search, tools.live_docs_search],
        runtime=runtime,
        node_id=node_id or "doc_research",
    )

    if grounding == GITHUB:
        github_agent = build_github_researcher(
            model,
            github_tools or [],
            runtime=runtime,
            node_id=node_id or "doc_research",
        )
        agents = [github_agent, docs_agent]
        entry_point = github_agent
    elif grounding == DOCS:
        agents = [docs_agent]
        entry_point = docs_agent
    else:
        local_agent = _local_researcher(
            model, local_tools or [], repo_dir, runtime=runtime, node_id=node_id or "doc_research"
        )
        agents = [local_agent, docs_agent]
        entry_point = local_agent

    return Swarm(
        agents,
        entry_point=entry_point,
        max_handoffs=20,
        max_iterations=20,
        execution_timeout=execution_timeout if execution_timeout is not None else 1800.0,
        node_timeout=node_timeout if node_timeout is not None else 600.0,
        repetitive_handoff_detection_window=8,
        repetitive_handoff_min_unique_agents=3,
    )
