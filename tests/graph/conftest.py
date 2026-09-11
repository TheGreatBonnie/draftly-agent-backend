"""Shared fixtures for graph end-to-end tests (StubModel, no model keys)."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import pytest

from draftly.agents.schemas import (
    AnswerDraft,
    ChangelogEntry,
    DeliveryReceipt,
    DocChangePlan,
    EventClassification,
    EvidenceBundle,
    ImpactAnalysis,
    NotifyReceipt,
)
from draftly.app.composition.tools import build_tools
from tests.stub_model import StubModel

PR_TASK = (
    '{"event_id": "e-123", "event_type": "pull_request.opened", '
    '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
    '"pull_request": {"number": 7, "title": "Fix widget", "sha": "abc"}}'
)

ISSUE_TASK = (
    '{"event_id": "i-123", "event_type": "issues.opened", '
    '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
    '"issue": {"number": 3, "title": "Docs wrong"}}'
)

SUPPORT_TASK = (
    '{"event_id": "s-123", "event_type": "slack.message", '
    '"project_id": "proj-1", "source": "slack", '
    '"source_message_id": "m-1", "repository": null, '
    '"question": "How do I configure retries?"}'
)

RELEASE_TASK = (
    '{"event_id": "r-123", "event_type": "release.published", '
    '"project_id": "proj-1", "repository": "TheGreatBonnie/authly", "actor": "dev", '
    '"release": {"tag_name": "v2.0.0", "name": "v2.0.0", "body": "Added OAuth support.", '
    '"html_url": "https://github.com/TheGreatBonnie/authly/releases/tag/v2.0.0"}}'
)


def stub_model() -> StubModel:
    """One StubModel scripted for every structured-output agent."""
    return StubModel(
        structured_outputs={
            EventClassification: {
                "surface": "pull_request",
                "change_type": "bug_fix",
                "urgency": "medium",
                "reason": "code changed",
            },
            EvidenceBundle: {
                "items": [{"id": "docs/widgets.md", "topic": "widgets"}],
                "summary": "widgets documentation evidence",
            },
            ImpactAnalysis: {
                "action": "update",
                "affected_documents": ["docs/widgets.md"],
                "rationale": "behavior changed",
            },
            DocChangePlan: {
                "repository": "acme/api",
                "branch": "docs/update-widgets",
                "files": [
                    {
                        "path": "docs/widgets.md",
                        "content": "widgets docs/widgets.md " * 40,
                        "action": "update",
                    }
                ],
            },
            AnswerDraft: {
                "content": "widgets docs/widgets.md " * 40,
                "sources": ["docs/widgets.md"],
            },
            DeliveryReceipt: {
                "delivered_to": "pr://acme/api/8",
                "surface": "pull_request",
                "reference": "https://github/acme/api/pull/8",
                "status": "completed",
            },
            ChangelogEntry: {
                "version": "v2.0.0",
                "date": "2026-09-04",
                "entries": [{"category": "Added", "text": "OAuth support."}],
                "raw_markdown": "## [v2.0.0] - 2026-09-04\n\n### Added\n- OAuth support.\n",
            },
            NotifyReceipt: {
                "should_notify": True,
                "kind": "gap_detected",
                "body": "Draftly will generate docs for this PR:\n- docs/widgets.md",
            },
        }
    )


@pytest.fixture
def tools():
    return build_tools()


@pytest.fixture
def model():
    return stub_model()


class _RecordingCommenter:
    """Deterministic commenter recording create_comment calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def create_comment(
        self,
        repository: str,
        pull_request_number: int,
        body: str,
    ) -> dict:
        self.calls.append((repository, pull_request_number, body))
        return {
            "id": 1,
            "html_url": f"https://github/{repository}/pull/{pull_request_number}#issuecomment-1",
        }


@pytest.fixture
def comment_factory():
    """Injected comment factory for PR graph runs; records posted comments.

    Always returns the same recording commenter so a default graph build can
    assert a single post (idempotency) without touching GitHub.
    """
    commenter = _RecordingCommenter()
    return lambda: commenter, commenter


@pytest.fixture
def tmp_sessions(tmp_path):
    return str(tmp_path / "sessions")


class _GraphBuilderCapture:
    """Records every application-agent builder call plus ReviewGate creation."""

    def __init__(self) -> None:
        self.builder_calls: list[tuple[str, dict[str, Any]]] = []
        self.review_gates: list[Any] = []

    def wrap(self, label: str, fn: Any) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.builder_calls.append((label, kwargs))
            return fn(*args, **kwargs)

        return wrapper


# Agent builders referenced by the surface graphs. Docs/issue/support builders
# are imported inside the graph functions (patch the source module); content
# builders are module-level imports (patch the content graph module itself).
_AGENT_BUILDER_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("draftly.agents.shared.classifier", "build_classifier", "classifier"),
    ("draftly.agents.shared.context", "build_context_agent", "context"),
    ("draftly.agents.shared.delivery", "build_delivery_agent", "delivery"),
    ("draftly.agents.documentation.analyzer", "build_impact_agent", "impact"),
    ("draftly.agents.documentation.changelog", "build_changelog_agent", "changelog"),
    ("draftly.agents.documentation.context", "build_doc_context_agent", "doc_context"),
    (
        "draftly.agents.documentation.research_swarm",
        "build_doc_research_swarm",
        "doc_research_swarm",
    ),
    ("draftly.agents.documentation.writer", "build_writer_agent", "writer"),
    ("draftly.agents.github.context", "build_issue_context_agent", "issue_context"),
    ("draftly.agents.github.issue_analyzer", "build_issue_analyzer", "issue_analyzer"),
    ("draftly.agents.github.issue_responder", "build_issue_responder", "issue_responder"),
    (
        "draftly.agents.github.research_swarm",
        "build_issue_research_swarm",
        "issue_research_swarm",
    ),
    ("draftly.agents.notify", "build_notify_agent", "notify"),
    ("draftly.agents.support.answer_writer", "build_answer_writer", "answer_writer"),
    (
        "draftly.agents.support.question_analyzer",
        "build_question_analyzer",
        "question_analyzer",
    ),
    (
        "draftly.agents.support.research_swarm",
        "build_support_research_swarm",
        "support_research_swarm",
    ),
    (
        "draftly.agents.support.solution_researcher",
        "build_solution_researcher",
        "solution_researcher",
    ),
    (
        "draftly.orchestration.graphs.content_graph",
        "build_content_strategist",
        "content_strategist",
    ),
    ("draftly.orchestration.graphs.content_graph", "build_blog_writer", "blog_writer"),
    ("draftly.orchestration.graphs.content_graph", "build_social_adapter", "social_adapter"),
    (
        "draftly.orchestration.graphs.content_graph",
        "build_content_grounding_judge",
        "content_judge",
    ),
)

_REVIEW_GATE_MODULES = (
    "draftly.orchestration.graphs.documentation_graph",
    "draftly.orchestration.graphs.issue_graph",
    "draftly.orchestration.graphs.support_graph",
    "draftly.orchestration.graphs.content_graph",
)


def _patch_agent_builders(monkeypatch: pytest.MonkeyPatch, capture: _GraphBuilderCapture) -> None:
    for module_name, attr, label in _AGENT_BUILDER_TARGETS:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, attr, capture.wrap(label, getattr(module, attr)))


def _patch_review_gates(monkeypatch: pytest.MonkeyPatch, capture: _GraphBuilderCapture) -> None:
    from draftly.orchestration.hooks.review_gate import ReviewGate

    class _SpyGate(ReviewGate):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            capture.review_gates.append(self)
            super().__init__(*args, **kwargs)

    for module_name in _REVIEW_GATE_MODULES:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "ReviewGate", _SpyGate)


class _GraphSurfaceFixture:
    """Per-surface build fixture asserting the Task 6 steering contract.

    Builds the real graph via ``build_graph_for_run`` with a run-scoped
    ``SteeringRuntime`` while capturing each application-agent builder call,
    so assertions inspect factory arguments instead of private Strands SDK
    internals. Agent/review-gate modules are patched within each ``build``
    (a scoped ``MonkeyPatch``) so per-surface captures never collide.
    """

    def __init__(self, *, surface: str, deps: dict[str, Any]) -> None:
        self.surface = surface
        self._deps = deps
        self._capture: _GraphBuilderCapture | None = None
        self._runtime: Any = None
        self.graph: Any = None

    def build(self, *, surface: str | None = None, steering_enabled: bool = True) -> Any:
        from draftly.integrations.strands.graph import build_graph_for_run
        from draftly.steering.context import SteeringRuntime, SteeringRuntimeConfig

        target = surface or self.surface
        capture = _GraphBuilderCapture()
        self._capture = capture
        self._runtime = SteeringRuntime.from_context(
            run_id="test-run",
            surface=target,
            org_id="org-1",
            project_id="project-1",
            workflow_key="docs",
            config=SteeringRuntimeConfig(enabled=steering_enabled, enforcement_enabled=True),
        )
        with pytest.MonkeyPatch().context() as monkeypatch:
            _patch_agent_builders(monkeypatch, capture)
            _patch_review_gates(monkeypatch, capture)
            self.graph = build_graph_for_run(
                "test-run",
                surface=target,
                tools_registry=self._deps["tools"],
                model=self._deps["model"],
                hooks=[],
                agents=self._deps.get("agents"),
                storage_dir=self._deps["storage_dir"],
                audit_repo=self._deps.get("audit_repo"),
                memory=self._deps.get("memory"),
                publisher=self._deps.get("publisher"),
                jobs_repo=self._deps.get("jobs_repo"),
                grounding=self._deps.get("grounding", "local"),
                repo_dir=self._deps.get("repo_dir"),
                content_repository=self._deps.get("content_repository"),
                steering_runtime=self._runtime,
            )
        return self.graph

    def has_provider(self, graph: Any, provider_type: Any) -> bool:
        del provider_type
        assert self._capture is not None, "call build() first"
        return bool(self._capture.review_gates)

    def application_agents_have_steering(self, graph: Any) -> bool:
        assert self._capture is not None, "call build() first"
        assert self._capture.builder_calls, "no application agent builders ran"
        node_ids = set(graph.nodes)
        for label, kwargs in self._capture.builder_calls:
            runtime = kwargs.get("runtime")
            assert runtime is not None, f"{label} missing steering runtime"
            assert runtime.scope.run_id == "test-run", f"{label} wrong run scope"
            assert runtime.config.enabled is True, f"{label} steering disabled"
            node_id = kwargs.get("node_id")
            assert node_id, f"{label} missing node_id"
            assert node_id in node_ids, f"{label} node_id {node_id!r} is not a graph node"
            assert kwargs.get("agent_id"), f"{label} missing agent_id"
        return True


@pytest.fixture
def graph_builder_fixtures(model, tools, tmp_sessions):
    """Map every surface to an isolated graph-build fixture for Task 6."""

    deps: dict[str, Any] = {
        "tools": tools,
        "model": model,
        "storage_dir": tmp_sessions,
        "audit_repo": None,
        "memory": None,
        "publisher": None,
        "jobs_repo": None,
        "agents": None,
        "grounding": "local",
        "repo_dir": None,
        "content_repository": object(),
    }
    return {
        surface: _GraphSurfaceFixture(surface=surface, deps=deps)
        for surface in ("pull_request", "issue", "support", "slack", "discord", "content")
    }
