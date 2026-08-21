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

    assert build_writer_agent(
        stub_model, tools.documentation_engineer
    )._default_structured_output_model is DocChangePlan
    assert build_impact_agent(
        stub_model, tools.documentation
    )._default_structured_output_model is ImpactAnalysis
    assert build_reviewer_agent(
        stub_model, tools.documentation_reviewer
    )._default_structured_output_model is EvaluationResult
    assert build_answer_writer(
        stub_model, tools.support_engineer
    )._default_structured_output_model is AnswerDraft


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
    assert isinstance(
        agent._intervention_registry.handlers[0], HumanInTheLoop
    )


def test_skills_load_from_directory() -> None:
    from strands.vended_plugins.skills import Skill

    skills_root = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "draftly"
        / "skills"
    )
    skills = Skill.from_directory(skills_root)
    names = {skill.name for skill in skills}
    assert len(skills) == 20
    assert "github-pr-analysis" in names
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
