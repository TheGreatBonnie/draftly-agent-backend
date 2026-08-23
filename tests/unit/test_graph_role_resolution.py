"""Graph builders must resolve every agent via its ROLE_POLICIES role."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from draftly.integrations.strands import graph as strands_graph


def _builder_for(graph_id: str):
    """Resolve builders via the production entrypoint.

    ``integrations.strands`` initializes eagerly, so graph builders must
    be imported through it (direct orchestration-graph imports hit a
    pre-existing circular import).
    """
    if graph_id == "pull_request":
        return strands_graph._BUILDERS[graph_id]
    return {
        "support": strands_graph.build_support_graph,
        "issue": strands_graph.build_issue_graph,
    }[graph_id]


class _TolerantModel:
    """Duck-type stand-in absorbing Strands Model attribute probes."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


@dataclass
class RecordingResolver:
    """Stands in for RoleAwareModelResolver; records for_role calls."""

    roles: list[str] = field(default_factory=list)

    def for_role(self, role: str, **_: Any) -> _TolerantModel:
        self.roles.append(role)
        return _TolerantModel()

    def __getattr__(self, name: str):
        if name.startswith("__") or name == "_roles":
            raise AttributeError(name)
        return None


@dataclass
class FakeToolsRegistry:
    """Every attribute is a tool group (iterable) like the real registry."""

    def __getattr__(self, name: str) -> list:
        def _tool() -> None:
            raise AssertionError("tools are never invoked during build")

        _tool.__name__ = f"fake_{name}"
        return [_tool]


EXPECTED_ROLES: dict[str, set[str]] = {
    "pull_request": {
        "classifier",
        "context",
        "documentation_engineer",
        "support_engineer",
        "research",
        "github_delivery",
        "github_intelligence",
    },
    "support": {
        "classifier",
        "context",
        "research",
        "support_engineer",
        "documentation_engineer",
        "github_delivery",
    },
    "issue": {
        "classifier",
        "context",
        "research",
        "github_intelligence",
        "support_engineer",
        "documentation_engineer",
    },
}


@pytest.mark.parametrize("graph_id", ["pull_request", "support", "issue"])
def test_graph_resolves_expected_roles(graph_id: str) -> None:
    resolver = RecordingResolver()

    _builder_for(graph_id)(
        session_manager=None,
        tools_registry=FakeToolsRegistry(),
        model=resolver,
    )

    assert set(resolver.roles) == EXPECTED_ROLES[graph_id]


def test_shared_model_passthrough_still_supported() -> None:
    """Raw model objects (no for_role) must keep working untouched."""

    builder = _builder_for("issue")(
        session_manager=None,
        tools_registry=FakeToolsRegistry(),
        model="shared-raw-model",
    )

    assert builder is not None
