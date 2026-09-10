"""Centralized agent constructor and production construction coverage.

Covers plan Task 5: every production ``Agent(...)`` site must route through
``build_draftly_agent`` and every built agent must carry exactly one
``DraftlySteeringHandler`` (a no-op on disabled runtimes for legacy callers).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from strands import Agent
from strands.plugins import Plugin
from strands.vended_interventions.hitl import HumanInTheLoop

from draftly.agents.factory import build_draftly_agent
from draftly.agents.schemas import DeliveryReceipt, EventClassification
from draftly.agents.shared import build_classifier, build_delivery_agent
from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import AgentRole
from draftly.steering.handler import DraftlySteeringHandler
from tests.stub_model import StubModel


class ExistingPlugin(Plugin):
    name = "existing"

    marker = object()


def agent_plugins(agent: Agent) -> list[Plugin]:
    return list(agent._plugin_registry._plugins.values())


@pytest.fixture
def runtime() -> SteeringRuntime:
    audit = Mock()
    audit.record_step = AsyncMock()
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root="",
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=True),
        audit=audit,
    )


def test_constructor_preserves_plugins_and_adds_one_steering_handler(runtime):
    existing_plugin = ExistingPlugin()
    agent = build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt="write",
        model=StubModel(),
        plugins=[existing_plugin],
        runtime=runtime,
        agent_id="writer",
        node_id="write",
    )
    plugins = agent_plugins(agent)
    assert existing_plugin in plugins
    assert sum(isinstance(p, DraftlySteeringHandler) for p in plugins) == 1


def test_constructor_derives_agent_scoped_runtime_identity(runtime):
    agent = build_draftly_agent(
        role=AgentRole.DELIVERY,
        system_prompt="deliver",
        model=StubModel(),
        runtime=runtime,
        agent_id="delivery-1",
        node_id="pr",
    )
    handler = next(
        p for p in agent_plugins(agent) if isinstance(p, DraftlySteeringHandler)
    )
    identity = handler.runtime.identity
    assert identity is not None
    assert identity.agent_id == "delivery-1"
    assert identity.node_id == "pr"
    assert identity.role is AgentRole.DELIVERY


def test_constructor_preserves_structured_output_model(runtime):
    agent = build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt="deliver",
        model=StubModel(),
        runtime=runtime,
        agent_id="writer-1",
        node_id="write",
        structured_output_model=DeliveryReceipt,
        name="delivery",
    )
    assert isinstance(agent, Agent)
    assert agent._default_structured_output_model is DeliveryReceipt
    assert agent.name == "delivery"


def test_constructor_with_disabled_runtime_adds_noop_handler():
    agent = build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt="write",
        model=StubModel(),
        runtime=SteeringRuntime.disabled(),
        agent_id="legacy",
        node_id="legacy",
    )
    plugins = agent_plugins(agent)
    assert sum(isinstance(p, DraftlySteeringHandler) for p in plugins) == 1
    handler = next(p for p in plugins if isinstance(p, DraftlySteeringHandler))
    assert not handler.runtime.enabled


def test_constructor_works_for_every_agent_role():
    for role in AgentRole:
        agent = build_draftly_agent(
            role=role,
            system_prompt=f"prompt for {role.value}",
            model=StubModel(),
            runtime=SteeringRuntime.disabled(),
            agent_id=f"agent-{role.value}",
            node_id=f"node-{role.value}",
        )
        handlers = [
            p for p in agent_plugins(agent) if isinstance(p, DraftlySteeringHandler)
        ]
        assert len(handlers) == 1
        assert handlers[0].policy.side_effecting is (
            role in {AgentRole.DELIVERY, AgentRole.SUPPORT}
        )


def test_all_production_agent_sites_use_the_constructor():
    source_root = Path("src/draftly")
    offenders = []
    for path in source_root.rglob("*.py"):
        if path.name in {"factory.py", "handler.py"}:
            continue
        if "Agent(" in path.read_text() and "tests" not in str(path):
            offenders.append(str(path))
    assert offenders == []


def test_migrated_classifier_factory_works_without_runtime():
    agent = build_classifier(StubModel())
    assert isinstance(agent, Agent)
    assert agent.name == "event_classifier"
    assert agent._default_structured_output_model is EventClassification
    plugins = agent_plugins(agent)
    assert sum(isinstance(p, DraftlySteeringHandler) for p in plugins) == 1


def test_migrated_delivery_factory_preserves_hitl_interventions():
    agent = build_delivery_agent(StubModel(), [], hitl=True)
    assert isinstance(agent._intervention_registry.handlers[0], HumanInTheLoop)
    plugins = agent_plugins(agent)
    assert sum(isinstance(p, DraftlySteeringHandler) for p in plugins) == 1
