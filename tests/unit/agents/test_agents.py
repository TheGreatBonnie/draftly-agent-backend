"""Phase 3 tests: agents, swarm construction, and skill loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from strands import Agent
from strands.multiagent import Swarm

from draftly.agents.documentation import (
    build_auditor_agent,
    build_documentation_researcher,
    build_impact_agent,
    build_reviewer_agent,
    build_writer_agent,
)
from draftly.agents.github import (
    build_issue_analyzer,
    build_issue_researcher,
    build_issue_responder,
)
from draftly.agents.schemas import (
    AnswerDraft,
    DocChangePlan,
    EvaluationResult,
    EventClassification,
    ImpactAnalysis,
)
from draftly.agents.shared import (
    build_classifier,
    build_context_agent,
    build_delivery_agent,
    build_github_delivery_agent,
    build_memory_curator,
)
from draftly.agents.subagents import build_research_swarm
from draftly.agents.support import (
    build_answer_writer,
    build_question_analyzer,
    build_solution_researcher,
    build_support_reviewer,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draftly.app.composition.tools import build_tools  # noqa: E402
from tests.stub_model import StubModel  # noqa: E402


@pytest.fixture
def stub_model() -> StubModel:
    return StubModel()


def test_classifier_agent_uses_structured_output(stub_model: StubModel) -> None:
    agent = build_classifier(stub_model)
    assert isinstance(agent, Agent)
    assert agent.name == "event_classifier"
    assert agent._default_structured_output_model is EventClassification


def test_stub_model_structured_output_roundtrip() -> None:
    model = StubModel(
        output={
            "surface": "pull_request",
            "change_type": "api_change",
            "urgency": "high",
            "reason": "breaking API change",
        }
    )

    async def run() -> EventClassification:
        async for event in model.structured_output(
            EventClassification,
            [],
        ):
            return event["output"]
        raise AssertionError("no output yielded")

    import asyncio

    result = asyncio.run(run())
    assert result.surface == "pull_request"
    assert result.change_type == "api_change"
    assert result.urgency == "high"


def test_surface_agents_construct(stub_model: StubModel) -> None:
    tools = build_tools()
    for agent in (
        build_context_agent(stub_model, tools.documentation),
        build_impact_agent(stub_model, tools.documentation),
        build_writer_agent(stub_model, tools.documentation_engineer),
        build_reviewer_agent(stub_model, tools.documentation_reviewer),
        build_auditor_agent(stub_model, tools.documentation_reviewer),
        build_documentation_researcher(stub_model, tools.research),
        build_answer_writer(stub_model, tools.support_engineer),
        build_question_analyzer(stub_model),
        build_solution_researcher(stub_model, tools.support_engineer),
        build_support_reviewer(stub_model, tools.support_reviewer),
        build_issue_analyzer(stub_model),
        build_issue_researcher(stub_model, tools.github_intelligence),
        build_issue_responder(stub_model, tools.github_intelligence),
        build_github_delivery_agent(stub_model, tools.github_delivery),
        build_memory_curator(stub_model),
    ):
        assert isinstance(agent, Agent)
        assert agent.name

    assert (
        build_writer_agent(
            stub_model, tools.documentation_engineer
        )._default_structured_output_model
        is DocChangePlan
    )
    assert (
        build_impact_agent(stub_model, tools.documentation)._default_structured_output_model
        is ImpactAnalysis
    )
    assert (
        build_reviewer_agent(
            stub_model, tools.documentation_reviewer
        )._default_structured_output_model
        is EvaluationResult
    )
    assert (
        build_answer_writer(stub_model, tools.support_engineer)._default_structured_output_model
        is AnswerDraft
    )


def test_agent_registry_contains_all_active_graph_factories() -> None:
    from draftly.app.composition.agents import build_agents

    registry = build_agents(models=None, tools=None)
    required = {
        "classifier",
        "context_agent",
        "delivery_agent",
        "impact_agent",
        "writer_agent",
        "answer_writer",
        "question_analyzer",
        "solution_researcher",
        "issue_analyzer",
        "issue_responder",
        "research_swarm_factory",
        "documentation_context",
        "documentation_research_swarm",
        "issue_context",
        "issue_research_swarm",
        "support_research_swarm",
        "changelog_agent",
    }
    assert required <= set(registry.__dataclass_fields__)
    assert all(callable(getattr(registry, name)) for name in required)


def test_writer_agent_registers_authoring_skills(stub_model: StubModel) -> None:
    from draftly.agents.documentation.writer import build_writer_agent

    tools = build_tools()
    agent = build_writer_agent(stub_model, tools.documentation_engineer)

    plugin = agent._plugin_registry._plugins.get("agent_skills")
    assert plugin is not None
    names = {skill.name for skill in plugin.get_available_skills(agent)}
    assert {"documentation-update", "documentation-generation"} <= names


def test_pr_research_and_delivery_agents_register_workflow_skills(
    stub_model: StubModel,
) -> None:
    from draftly.agents.documentation.research_swarm import build_doc_research_swarm

    tools = build_tools()

    swarm = build_doc_research_swarm(
        stub_model,
        tools,
        local_tools=tools.documentation,
    )
    local = swarm.nodes["local_repo_researcher"].executor
    plugin = local._plugin_registry._plugins.get("agent_skills")
    assert plugin is not None
    assert "github-pr-analysis" in {
        skill.name for skill in plugin.get_available_skills(local)
    }

    delivery = build_delivery_agent(stub_model, tools.github_delivery, hitl=False)
    plugin = delivery._plugin_registry._plugins.get("agent_skills")
    assert plugin is not None
    assert "github-delivery" in {
        skill.name for skill in plugin.get_available_skills(delivery)
    }


def test_issue_surface_agents_register_skills(stub_model: StubModel) -> None:
    from draftly.agents.github.context import build_issue_context_agent
    from draftly.agents.github.issue_analyzer import build_issue_analyzer
    from draftly.agents.github.issue_responder import build_issue_responder
    from draftly.agents.github.research_swarm import build_issue_research_swarm
    from draftly.agents.support.answer_writer import build_answer_writer
    from draftly.agents.support.question_analyzer import build_question_analyzer
    from draftly.agents.support.solution_researcher import build_solution_researcher

    tools = build_tools()

    def skill_names(agent: Agent) -> set[str]:
        plugin = agent._plugin_registry._plugins.get("agent_skills")
        assert plugin is not None
        return {skill.name for skill in plugin.get_available_skills(agent)}

    context = build_issue_context_agent(stub_model, tools.documentation)
    assert {"repository-analysis", "github-issue-analysis"} <= skill_names(context)

    analyzer = build_issue_analyzer(stub_model)
    assert {"github-issue-analysis", "documentation-gap-detection"} <= skill_names(analyzer)

    responder = build_issue_responder(stub_model, tools.github_intelligence)
    assert {"github-issue-response"} <= skill_names(responder)

    answer = build_answer_writer(stub_model, tools.support_engineer)
    assert {"support-answering"} <= skill_names(answer)

    support_analyzer = build_question_analyzer(stub_model)
    assert {"support-triage", "documentation-gap-detection"} <= skill_names(
        support_analyzer
    )

    solution = build_solution_researcher(stub_model, tools.support_engineer)
    assert {"repository-analysis", "support-answering"} <= skill_names(solution)

    swarm = build_issue_research_swarm(
        stub_model,
        tools,
        local_tools=tools.documentation,
    )
    local_node = swarm.nodes["local_repo_researcher"]
    assert {node.node_id for node in swarm.nodes.values()} >= {"local_repo_researcher"}
    assert {"github-issue-analysis", "repository-analysis"} <= skill_names(local_node.executor)


def test_research_swarm_construction(stub_model: StubModel) -> None:
    tools = build_tools()
    swarm = build_research_swarm(stub_model, tools)
    assert isinstance(swarm, Swarm)
    names = {node.node_id for node in swarm.nodes.values()}
    assert names == {
        "github_researcher",
        "slack_researcher",
        "discord_researcher",
        "docs_researcher",
    }
    assert swarm.max_handoffs == 20
    assert swarm.max_iterations == 20
    assert swarm.repetitive_handoff_detection_window == 8


def test_delivery_agent_hitl_intervention(stub_model: StubModel) -> None:
    from strands.vended_interventions.hitl import HumanInTheLoop

    tools = build_tools()
    agent = build_delivery_agent(stub_model, tools.github_delivery, hitl=True)
    assert isinstance(agent._intervention_registry.handlers[0], HumanInTheLoop)


def test_skills_load_from_directory() -> None:
    from strands.vended_plugins.skills import Skill

    skills_root = Path(__file__).resolve().parents[3] / "src" / "draftly" / "skills"
    skills = Skill.from_directory(skills_root)
    names = {skill.name for skill in skills}
    assert len(skills) == 22
    assert "content-production" in names
    assert "github-pr-analysis" in names
    assert "github-issue-response" in names
    assert "documentation-generation" in names
    assert "support-answering" in names
    for skill in skills:
        assert skill.description


def test_policy_prompts_load() -> None:
    from draftly.agents.prompts import build_prompt, load_policy

    assert load_policy("documentation_policy")
    assert "grounded" in load_policy("documentation_policy").lower()
    rendered = build_prompt(
        "{documentation_policy}",
        documentation_policy="documentation_policy",
    )
    assert "grounded" in rendered
