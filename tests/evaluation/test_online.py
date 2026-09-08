"""Offline tests for the live evaluation path — no model keys required.

These cover the deterministic pieces that make live evaluation work:
tool-usage trajectory extraction from a graph result, the online task
function that invokes a real Strands client and flattens tool trajectories,
and the NodeToolCalled / build_live_evaluators wiring. A StubModel is enough;
no LLM judges are exercised here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from strands_evals import Case

from draftly.evaluation.online import (
    _collect_rel_paths,
    build_event,
    build_online_task,
    extract_authored_content,
    extract_delivery_summary,
    extract_output_text,
)
from draftly.evaluation.runner import (
    ExpectedAuthoringAction,
    ExpectedContains,
    ExpectedDelivered,
    ExpectedToolCalled,
    NodeToolCalled,
    build_live_evaluators,
)
from draftly.evaluation.trajectory import extract_trajectories, flatten_trajectory

# ---------------------------------------------------------------------------
# Stubs mirroring the Strands graph-result shape (NodeResult / AgentResult /
# EventLoopMetrics.tool_metrics).
# ---------------------------------------------------------------------------


class StubTool:
    def __init__(self, name: str, *, input: dict | None = None):
        self.name = name
        self.input = input or {}


class StubToolMetric:
    def __init__(self, name: str, *, call_count=1, success_count=1, error_count=0):
        self.tool = StubTool(name)
        self.call_count = call_count
        self.success_count = success_count
        self.error_count = error_count


class StubMetrics:
    def __init__(self, tool_names: list[str]):
        self.tool_metrics = {n: StubToolMetric(n) for n in tool_names}


class StubAgentResult:
    metrics: StubMetrics

    def __init__(
        self,
        tool_names: list[str],
        *,
        text: str = "final",
        structured_output: Any = None,
    ):
        self.metrics = StubMetrics(tool_names)
        self._text = text
        self.structured_output = structured_output

    def __str__(self) -> str:
        return self._text


class StubNode:
    node_id: str

    def __init__(self, node_id: str, agent_results: list[StubAgentResult]):
        self.node_id = node_id
        self._agent_results = agent_results

    def get_agent_results(self):
        return self._agent_results


class StubGraphResult:
    def __init__(self, nodes: list[StubNode]):
        self.execution_order = nodes


def _real_node_result(agent_result: Any) -> Any:
    """Wrap an AgentResult in a real Strands NodeResult."""
    from strands.multiagent.base import NodeResult, Status

    return NodeResult(result=agent_result, status=Status.COMPLETED)


def _real_graph_result(nodes: list[tuple[str, Any]]) -> Any:
    """Build a real Strands GraphResult whose ``execution_order`` holds GraphNode objects.

    This mirrors the actual ``GraphResult`` returned by ``StrandsClient.invoke``:
    ``execution_order`` is a ``list[GraphNode]`` (plain dataclass), NOT
    ``NodeResult``. Extraction code must resolve the ``NodeResult`` via
    ``GraphNode.result`` and must NOT call ``get_agent_results()`` on the
    GraphNode itself (it has no such method) — that was the live-eval crash.
    """
    from strands.multiagent.base import MultiAgentResult, Status
    from strands.multiagent.graph import GraphNode, GraphResult

    results: dict[str, Any] = {}
    order: list[Any] = []
    for node_id, agent_result in nodes:
        node_result = _real_node_result(agent_result)
        results[node_id] = node_result
        gn = GraphNode(node_id=node_id, executor=None)
        gn.execution_status = Status.COMPLETED
        gn.result = node_result
        order.append(gn)

    return GraphResult(
        status=Status.COMPLETED,
        results=MultiAgentResult(results=results).results,
        execution_order=order,
    )


def _real_agent_result(text: str = "final", tool_names: list[str] | None = None) -> Any:
    from strands.agent.agent_result import AgentResult
    from strands.telemetry.metrics import EventLoopMetrics, ToolMetrics
    from strands.types.content import ContentBlock, Message

    metrics = EventLoopMetrics()
    for i, name in enumerate(tool_names or []):
        tool_use = {"name": name, "input": {}, "toolUseId": f"call_{i}"}
        metrics.tool_metrics[name] = ToolMetrics(
            tool=tool_use, call_count=1, success_count=1, error_count=0
        )

    return AgentResult(
        stop_reason="end_turn",
        message=Message(content=[ContentBlock(text=text)], role="assistant"),
        metrics=metrics,
        state=None,
    )


def _case(name: str, input: str, expected: str, metadata: dict) -> Case:
    return Case(name=name, input=input, expected_output=expected, metadata=metadata)


# ---------------------------------------------------------------------------
# Trajectory extraction
# ---------------------------------------------------------------------------


def test_extract_trajectories_maps_nodes_to_tools() -> None:
    graph = StubGraphResult(
        [
            StubNode("context", [StubAgentResult(["semantic_search", "keyword_search"])]),
            StubNode("research", [StubAgentResult(["code_search"])]),
            StubNode("answer", [StubAgentResult([])]),
        ]
    )

    trajectories = extract_trajectories(graph)

    assert list(trajectories) == ["context", "research", "answer"]
    assert trajectories["context"] == [
        {
            "name": "semantic_search",
            "input": {},
            "call_count": 1,
            "success_count": 1,
            "error_count": 0,
        },
        {
            "name": "keyword_search",
            "input": {},
            "call_count": 1,
            "success_count": 1,
            "error_count": 0,
        },
    ]
    assert trajectories["answer"] == []
    assert flatten_trajectory(trajectories) == [
        "semantic_search",
        "keyword_search",
        "code_search",
    ]


def test_extract_trajectories_includes_nodes_without_tools() -> None:
    graph = StubGraphResult([StubNode("answer", [StubAgentResult([])])])
    trajectories = extract_trajectories(graph)
    assert list(trajectories) == ["answer"]
    assert trajectories["answer"] == []


# ---------------------------------------------------------------------------
# Event building per surface
# ---------------------------------------------------------------------------


def test_build_event_normalizes_surfaces() -> None:
    case = _case("retry", "question?", "expected", {"project_id": "proj-1"})

    pr = build_event(case, "pull_request")
    assert pr["event_type"] == "pull_request.opened"
    assert pr["pull_request"]["title"] == "question?"

    issue = build_event(case, "issue")
    assert issue["event_type"] == "issues.opened"
    assert issue["issue"]["body"] == "question?"

    support = build_event(case, "support")
    assert support["event_type"] == "slack.message"
    assert support["source"] == "slack"
    assert support["question"] == "question?"
def test_build_event_merges_pr_authoring_cases() -> None:
    case = _case(
        "pkce-oauth-update",
        "Merge PR: add PKCE to OAuth",
        "docs must mention PKCE",
        {
            "event_type": "pull_request.merged",
            "changed_files": [{"path": "src/authly/oauth.py", "change": "adds PKCE"}],
        },
    )

    event = build_event(case, "pull_request")

    assert event["event_type"] == "pull_request.merged"
    assert event["pull_request"]["merged"] is True
    assert event["pull_request"]["changed_files"] == ["src/authly/oauth.py"]
    assert event["pull_request"]["changed_file_details"] == [
        {"path": "src/authly/oauth.py", "change": "adds PKCE"}
    ]
    assert "base" in event["pull_request"] and "head" in event["pull_request"]


def test_extract_authored_content_reads_writer_plans() -> None:
    import json

    plan = json.dumps(
        {
            "files": [
                {"path": "docs/oauth.md", "content": "PKCE is now required.", "action": "update"}
            ],
            "summary": "update oauth docs",
        }
    )
    graph = StubGraphResult(
        [
            StubNode("update", [StubAgentResult([], text=plan)]),
            StubNode("answer", [StubAgentResult([], text="answer text")]),
        ]
    )

    assert extract_authored_content(graph) == "PKCE is now required."
    # authoring output wins over the answer node
    assert extract_authored_content(graph) or extract_output_text(graph) == "PKCE is now required."


def test_extract_authored_content_reads_structured_output() -> None:
    """Writer DocChangePlan is a validated structured_output, not raw message text."""
    from draftly.agents.schemas import DocChangePlan

    plan = DocChangePlan(
        repository="TheGreatBonnie/authly",
        branch="docs/oauth-fix",
        files=[
            {
                "path": "docs/how-to/oauth-authorization-url.md",
                "content": "PKCE is required.",
                "action": "update",
            }
        ],
        commit_message="docs: add PKCE",
        summary="document PKCE",
    )
    graph = StubGraphResult(
        [
            StubNode("update", [StubAgentResult([], structured_output=plan)]),
            StubNode("answer", [StubAgentResult([], text="answer text")]),
        ]
    )

    assert extract_authored_content(graph) == "PKCE is required."


def test_extract_authored_content_includes_changelog() -> None:
    """Release runs author BOTH docs and a changelog entry; the changelog node's
    ChangelogEntry.raw_markdown must be part of the scored output so
    expected_contains/correctness see the 'Changed (Breaking)'/'Added' artifacts
    (the release run failed expected_contains at 33% because only the migration
    docs were extracted)."""
    from draftly.agents.schemas import ChangelogEntry, DocChangePlan

    plan = DocChangePlan(
        repository="TheGreatBonnie/authly",
        branch="release-v2.0.0",
        files=[
            {
                "path": "docs/how-to/rbac.md",
                "content": "RBAC guide content.",
                "action": "create",
            }
        ],
        commit_message="docs: add RBAC guide",
        summary="create rbac guide",
    )
    changelog = ChangelogEntry(
        version="v2.0.0",
        date="2026-09-05",
        entries=[
            {"category": "Changed", "text": "BREAKING: Removed API key authentication"},
            {"category": "Added", "text": "RBAC with organization scoping"},
        ],
        raw_markdown=(
            "## [v2.0.0] - 2026-09-05\n\n"
            "### Changed (Breaking)\n- Removed API key authentication\n\n"
            "### Added\n- RBAC with organization scoping"
        ),
    )
    graph = StubGraphResult(
        [
            StubNode("create", [StubAgentResult([], structured_output=plan)]),
            StubNode("changelog", [StubAgentResult([], structured_output=changelog)]),
        ]
    )

    text = extract_authored_content(graph)
    assert "RBAC guide content." in text
    assert "Changed (Breaking)" in text
    assert "### Added" in text


def test_doc_change_plan_requires_at_least_one_file() -> None:
    """An empty plan must FAIL validation, not silently validate to no files.

    This is what stops a Strands tool-input parse failure (`{ }` default) from
    being scored as a valid-but-empty DocChangePlan: the structured-output tool
    returns a retryable error to the model instead.
    """
    from pydantic import ValidationError

    from draftly.agents.schemas import DocChangePlan

    with pytest.raises(ValidationError):
        DocChangePlan()

    with pytest.raises(ValidationError):
        DocChangePlan(files=[])


def test_extract_authored_content_empty_without_writers() -> None:
    graph = StubGraphResult([StubNode("answer", [StubAgentResult([], text="answer text")])])
    assert extract_authored_content(graph) == ""


def test_expected_authoring_action_scores_behavior() -> None:
    evaluator = ExpectedAuthoringAction()

    # "none" + no authored content → pass
    hit = evaluator.evaluate(
        SimpleNamespace(metadata={"expected_action": "none"}, actual_output="")
    )[0]
    assert hit.test_pass is True
    # "none" + authored content → fail (eager authoring)
    miss = evaluator.evaluate(
        SimpleNamespace(metadata={"expected_action": "none"}, actual_output="unrequested docs")
    )[0]
    assert miss.test_pass is False
    # "update" + content → pass; "update" + empty → fail
    ok = evaluator.evaluate(
        SimpleNamespace(metadata={"expected_action": "update"}, actual_output="updated docs")
    )[0]
    assert ok.test_pass is True
    bad = evaluator.evaluate(
        SimpleNamespace(metadata={"expected_action": "update"}, actual_output="")
    )[0]
    assert bad.test_pass is False
    # no expected_action → trivial pass
    skip = evaluator.evaluate(SimpleNamespace(metadata={}, actual_output=""))[0]
    assert skip.test_pass is True


def test_expected_delivered_requires_receipt_for_authoring() -> None:
    evaluator = ExpectedDelivered()

    ok = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_action": "update"},
            actual_environment_state={"delivery_summary": "pr://acme/api/8"},
        )
    )[0]
    assert ok.test_pass is True
    # list-form EnvironmentState entries must also be read (the live online
    # task now emits actual_environment_state as list[EnvironmentState]).
    ok_list = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_action": "update"},
            actual_environment_state=[
                {"name": "context", "state": {"delivery_summary": "pr://acme/api/9"}}
            ],
        )
    )[0]
    assert ok_list.test_pass is True
    miss = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_action": "create"},
            actual_environment_state={"delivery_summary": ""},
        )
    )[0]
    assert miss.test_pass is False
    skip = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_action": "none"},
            actual_environment_state={"delivery_summary": ""},
        )
    )[0]
    assert skip.test_pass is True


def test_extract_delivery_summary_reads_deliver_node() -> None:
    graph = StubGraphResult([StubNode("deliver", [StubAgentResult([], text="pr://acme/api/8")])])
    assert extract_delivery_summary(graph) == "pr://acme/api/8"
    assert extract_delivery_summary(StubGraphResult([])) == ""


async def test_build_online_task_passes_review_policy() -> None:
    class PolicyClient(FakeClient):
        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            return StubGraphResult([StubNode("answer", [StubAgentResult([], text="ok")])])

    client = PolicyClient()
    task = build_online_task(client)
    case = _case(
        "risky-change",
        "Merge PR: breaking rename",
        "ok",
        {"surface": "pull_request", "review_policy": "always"},
    )
    await task(case)
    assert client.calls[0]["invocation_state"]["review_policy"] == "always"


async def test_build_online_task_uses_unique_run_id_per_invocation() -> None:
    """A fresh run_id prevents Strands from restoring a stale prior session."""
    client = FakeClient()
    task = build_online_task(client)
    case = _case(
        "oauth-auth",
        "PR: add OAuth",
        "ok",
        {"surface": "pull_request", "repo_dir": "/tmp/001"},
    )

    await task(case)
    await task(case)

    first, second = client.calls[0]["run_id"], client.calls[1]["run_id"]
    assert first.startswith("eval-oauth-auth-")
    assert first != second
    # The worktree repo is threaded through invocation state so the graph
    # resolves real scenario data rather than a fabricated event.
    assert client.calls[0]["invocation_state"]["repo_dir"] == "/tmp/001"


async def test_build_online_task_prefixes_case_runs_with_parent_run_id() -> None:
    client = FakeClient()
    task = build_online_task(client, run_id_prefix="evaluation-parent-42")
    case = _case(
        "oauth-auth",
        "PR: add OAuth",
        "ok",
        {"surface": "pull_request"},
    )

    await task(case)

    assert client.calls[0]["run_id"].startswith("evaluation-parent-42-oauth-auth-")


# ---------------------------------------------------------------------------
# NodeToolCalled + build_live_evaluators
# ---------------------------------------------------------------------------


def test_node_tool_called_reads_actual_interactions() -> None:
    case = SimpleNamespace(
        actual_interactions=[{"node_name": "context", "tools": ["semantic_search"]}]
    )

    hit = NodeToolCalled("context", ["semantic_search", "code_search"]).evaluate(case)[0]
    assert hit.test_pass is True
    assert hit.score == 1.0

    # At-least-one semantics: none of the listed tools present -> fail.
    miss = NodeToolCalled("context", ["code_search", "file_exists"]).evaluate(case)[0]
    assert miss.test_pass is False
    assert miss.score == 0.0


def test_build_live_evaluators_without_judge_model_is_offline() -> None:
    cases = [
        _case(
            "retry-configuration",
            "How do I configure retries?",
            "enable retries",
            {"surface": "pull_request", "expected_tools": ["semantic_search"]},
        )
    ]

    evaluators = build_live_evaluators(
        cases,
        judge_model=None,
        surface_required_tools={"context": ["semantic_search", "keyword_search"]},
    )

    names = [e.name for e in evaluators]
    assert "expected_contains" in names
    assert "expected_tools" in names
    assert "node:context" in names


def test_build_live_evaluators_with_judge_model_names_are_unique() -> None:
    cases = [
        _case(
            "oauth-authentication-add",
            "PR: add OAuth authentication",
            "OAuth must be documented",
            {
                "surface": "pull_request",
                "expected_tools": ["code_search"],
                "expected_action": "update",
            },
        )
    ]

    evaluators = build_live_evaluators(
        cases,
        judge_model=object(),
        surface_required_tools={
            "context": ["read_file", "list_directory", "git_status"],
            "research": ["code_search"],
            "impact": ["get_diff", "get_files", "code_search"],
        },
    )

    names = [e.name for e in evaluators]
    # Regression: four rubric-based OutputEvaluator judges previously all
    # defaulted to name="OutputEvaluator", which DeepEval rejects as a
    # duplicate within an experiment.
    assert len(names) == len(set(names)), f"duplicate evaluator names: {names}"
    for judge in ("completeness", "correctness", "groundedness", "relevance"):
        assert judge in names

    # Correctness and groundedness must grade against the real source change
    # (uses_environment_state=True) so they can verify claims rather than
    # hallucinate that real APIs are invented.
    by_name = {e.name: e for e in evaluators}
    assert by_name["correctness"].uses_environment_state is True
    assert by_name["groundedness"].uses_environment_state is True


def test_dataset_required_tools_drive_node_metrics() -> None:
    """Node-level metrics must come from the dataset's ``required_tools`` and be
    enforced with at-least-one semantics (one node:<name> evaluator per node)."""
    dataset_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "evaluation"
        / "datasets"
        / "documentation.json"
    )
    dataset = json.loads(dataset_path.read_text())
    # Datasets are list-wrapped (one file may hold multiple cases); surface-level
    # config like required_tools lives on the first case.
    dataset = dataset[0] if isinstance(dataset, list) else dataset
    required_tools = dataset["required_tools"]

    case = _case(
        "oauth-authentication-add",
        "PR: add OAuth authentication",
        "OAuth must be documented",
        {"surface": "pull_request", "expected_tools": ["read_file"]},
    )
    evaluators = build_live_evaluators(
        [case], judge_model=None, surface_required_tools=required_tools
    )
    node_evaluators = {e.name: e for e in evaluators if isinstance(e, NodeToolCalled)}
    # Exactly one evaluator per required node, named node:<name>.
    assert list(node_evaluators) == ["node:context", "node:research", "node:impact"]
    assert node_evaluators["node:context"].tools == required_tools["context"]
    assert node_evaluators["node:research"].tools == required_tools["research"]
    assert node_evaluators["node:impact"].tools == required_tools["impact"]

    # At-least-one semantics: a node passes if ANY of its listed tools was used.
    # Agents vary tool calls run-to-run, so a single per-tool requirement would
    # flake; requiring "any grounding tool" is robust. The observed live trace
    # attributes git_status/git_log/git_diff to research, read_file to context,
    # and only ImpactAnalysis to impact — all satisfy their node's any-of gate.
    hit = NodeToolCalled("context", required_tools["context"], name="hit").evaluate(
        SimpleNamespace(
            actual_interactions=[
                {"node_name": "context", "tools": ["read_file", "list_directory"]},
                {"node_name": "research", "tools": ["git_status", "git_log"]},
                {"node_name": "impact", "tools": ["ImpactAnalysis"]},
            ]
        )
    )[0]
    assert hit.test_pass is True

    miss = NodeToolCalled("context", required_tools["context"], name="miss").evaluate(
        SimpleNamespace(
            actual_interactions=[
                {"node_name": "context", "tools": ["EventClassification"]},
            ]
        )
    )[0]
    assert miss.test_pass is False
    assert miss.score == 0.0


def test_node_tool_called_empty_requirements_still_require_node_to_run() -> None:
    """Workflows that declare triage with NO required tools (support/slack/discord)
    must still fail when the node never ran — a misrouted graph (e.g. a support
    case running the docs graph) would otherwise pass node:triage trivially."""
    ran = NodeToolCalled("triage", [], name="node:triage").evaluate(
        SimpleNamespace(actual_interactions=[{"node_name": "triage", "tools": []}])
    )[0]
    assert ran.test_pass is True
    assert ran.score == 1.0

    never_ran = NodeToolCalled("triage", [], name="node:triage").evaluate(
        SimpleNamespace(
            actual_interactions=[
                {"node_name": "context", "tools": ["semantic_search"]},
                {"node_name": "impact", "tools": ["ImpactAnalysis"]},
            ]
        )
    )[0]
    assert never_ran.test_pass is False
    assert never_ran.score == 0.0
    assert "triage" in never_ran.reason


def test_zero_tool_node_appears_in_actual_interactions() -> None:
    """A node that executes without calling tools (answer, triage) must still
    surface in actual_interactions with tools: [] so empty-requirement
    NodeToolCalled checks can see it participated."""
    graph = StubGraphResult(
        [
            StubNode("impact", [StubAgentResult(["ImpactAnalysis"])]),
            StubNode("triage", [StubAgentResult([])]),
        ]
    )
    trajectories = extract_trajectories(graph)
    interactions = [
        {"node_name": node_id, "tools": [call["name"] for call in calls]}
        for node_id, calls in trajectories.items()
    ]
    assert interactions == [
        {"node_name": "impact", "tools": ["ImpactAnalysis"]},
        {"node_name": "triage", "tools": []},
    ]


# ---------------------------------------------------------------------------
# build_online_task with a fake Strands client
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return StubGraphResult(
            [
                StubNode("context", [StubAgentResult(["semantic_search"])]),
                StubNode("answer", [StubAgentResult([], text="expected answer")]),
            ]
        )


async def test_build_online_task_invokes_client_and_extracts() -> None:
    client = FakeClient()
    task = build_online_task(client)

    case = _case(
        "retry-configuration",
        "How do I configure retries?",
        "expected answer",
        {"surface": "pull_request", "expected_tools": ["semantic_search"]},
    )

    result = await task(case)

    assert result["output"] == "expected answer"
    assert result["trajectory"] == ["semantic_search"]
    assert result["interactions"] == [
        {"node_name": "context", "tools": ["semantic_search"]},
        {"node_name": "answer", "tools": []},
    ]
    assert client.calls[0]["surface"] == "pull_request"
    # Evaluation must override the docs graph's 180s per-node default so live
    # worktree runs get a node ceiling matching the harness intent (600s).
    assert client.calls[0]["node_timeout"] == 600.0


async def test_build_online_task_env_state_carries_real_diff() -> None:
    """The online task must surface the real PR diff in environment_state so
    the correctness/groundedness LLM judges can verify authored claims against
    the actual source (not just the thin PR description)."""
    client = FakeClient()
    task = build_online_task(client)

    case = _case(
        "oauth-authentication-add",
        "PR: add OAuth authentication",
        "OAuth must be documented",
        {
            "surface": "pull_request",
            "event_type": "pull_request.merged",
            "repo_dir": str(
                Path(__file__).resolve().parents[3]
                / "authly-scenarios"
                / "001-oauth-login"
            ),
            "pr_number": 101,
        },
    )

    result = await task(case)
    env = result["environment_state"]
    # strands_evals types actual_environment_state as list[EnvironmentState],
    # so each entry is a {name, state} pair (avoids Pydantic serialization
    # warnings and keeps the diff reachable by the LLM judges).
    assert isinstance(env, list) and len(env) >= 2
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "context" in by_name
    # The real diff from the authly worktree must be present for the judges.
    assert isinstance(by_name.get("diff"), str) and "oauth" in by_name["diff"]
    assert by_name.get("changed_files")
async def test_build_online_task_env_state_carries_issue_evidence() -> None:
    """The online task must surface the declared doc evidence *content* on the
    issue surface so correctness/groundedness judges can verify API claims
    against real docs instead of an empty environment."""

    client = FakeClient()
    task = build_online_task(client)

    case = _case(
        "authorization-error-permission-check",
        "Calling permissions.check() raises AuthorizationError",
        "None of the roles assigned to the user contains the requested permission",
        {
            "surface": "issue",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                },
                {
                    "id": "docs/how-to/check-permissions",
                    "url": "authly/docs/how-to/check-permissions.md",
                },
            ],
        },
    )

    result = await task(case)
    env = result["environment_state"]

    assert isinstance(env, list) and len(env) >= 2
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "context" in by_name
    assert "repo_dir" in by_name
    # The declared evidence docs must be read into the judge's environment so
    # API claims (e.g. permissions.list_for_user) are verifiable.
    ev = by_name.get("evidence_content")
    assert isinstance(ev, list) and len(ev) >= 2
    joined = " ".join(str(chunk) for chunk in ev)
    assert "list_for_user" in joined


def test_collect_rel_paths_dedupes_published_sources() -> None:
    """Cited evidence/source paths must be collected from the graph result's
    structured outputs (EvidenceBundle items, ImpactAnalysis.evidence,
    AnswerDraft.sources), normalized to repo-relative paths, and deduped."""
    from draftly.agents.schemas import AnswerDraft, EvidenceBundle, ImpactAnalysis

    graph = StubGraphResult(
        [
            StubNode(
                "context",
                [
                    StubAgentResult(
                        ["semantic_search"],
                        structured_output=EvidenceBundle(
                            items=[
                                {"path": "authly/docs/how-to/check-permissions.md"},
                                {"id": "docs/how-to/troubleshoot-errors"},
                            ]
                        ),
                    )
                ],
            ),
            StubNode(
                "impact",
                [
                    StubAgentResult(
                        ["ImpactAnalysis"],
                        structured_output=ImpactAnalysis(
                            action="answer",
                            evidence=[
                                "authly/src/authly/roles.py",
                                "docs/how-to/troubleshoot-errors.md",
                            ],
                        ),
                    )
                ],
            ),
            StubNode(
                "answer",
                [
                    StubAgentResult(
                        ["AnswerDraft"],
                        structured_output=AnswerDraft(
                            content="ok", sources=["authly/src/authly/roles.py"]
                        ),
                    )
                ],
            ),
        ]
    )

    paths = _collect_rel_paths(graph, repo_name="authly")

    assert "docs/how-to/check-permissions.md" in paths
    assert "docs/how-to/troubleshoot-errors.md" in paths
    assert "src/authly/roles.py" in paths
    # The same source appears in both impact and answer nodes; deduped.
    assert paths.count("src/authly/roles.py") == 1


def test_collect_rel_paths_returns_empty_without_citations() -> None:
    graph = StubGraphResult([])
    assert _collect_rel_paths(graph) == []


async def test_build_online_task_env_state_includes_cited_sources() -> None:
    """The online task must read cited source files (not just the seeded
    evidence docs) into evidence_content so groundedness can verify APIs the
    agent surfaced during research (e.g. authly.roles.assign())."""

    class Client:
        async def invoke(self, **kwargs):
            from draftly.agents.schemas import ImpactAnalysis

            return StubGraphResult(
                [
                    StubNode(
                        "impact",
                        [
                            StubAgentResult(
                                ["ImpactAnalysis"],
                                structured_output=ImpactAnalysis(
                                    action="answer",
                                    evidence=["authly/src/authly/roles.py"],
                                ),
                            )
                        ],
                    )
                ]
            )

    task = build_online_task(Client())
    case = _case(
        "authorization-error-permission-check",
        "Calling permissions.check() raises AuthorizationError",
        "expected",
        {
            "surface": "issue",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    result = await task(case)
    env = result["environment_state"]
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    ev = by_name.get("evidence_content")
    assert isinstance(ev, list) and len(ev) >= 2
    joined = " ".join(str(chunk) for chunk in ev)
    assert "def assign" in joined  # from roles.py, a cited source not in seed evidence
    assert any("roles.py" in chunk.get("path", "") for chunk in ev if isinstance(chunk, dict))


def test_build_event_support_carries_repo_grounding() -> None:
    """The support event must surface repo_dir + evidence (like the issue
    surface) so the support graph and the judges get concrete grounding."""

    case = _case(
        "invalid-email-or-password",
        "Login fails when email/password wrong",
        "The message condition is bad",
        {
            "surface": "support",
            "repository": "TheGreatBonnie/authly",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    ev = build_event(case, "support")
    support_payload = ev.get("support") or {}
    assert support_payload.get("repo_dir")
    assert support_payload.get("evidence")
    assert any(
        "troubleshoot-errors" in str(d)
        for d in support_payload.get("related_docs", [])
    )
    # The repository value from metadata must be preserved (not forced to None).
    assert ev.get("repository") == "TheGreatBonnie/authly"


async def test_build_online_task_env_state_grounds_support_surface() -> None:
    """The online task must feed evidence + cited-source content into
    <ActualEnvironmentState> for the support surface, mirroring the issue path,
    so groundedness/correctness can verify a support answer."""

    from draftly.agents.schemas import ImpactAnalysis

    class Client:
        async def invoke(self, **kwargs):
            return StubGraphResult(
                [
                    StubNode(
                        "impact",
                        [
                            StubAgentResult(
                                ["ImpactAnalysis"],
                                structured_output=ImpactAnalysis(
                                    action="answer",
                                    evidence=["authly/docs/reference/errors.md"],
                                ),
                            )
                        ],
                    )
                ]
            )

    task = build_online_task(Client())
    case = _case(
        "invalid-email-or-password",
        "Login fails with AuthenticationError: invalid email or password",
        "Which account is wrong?",
        {
            "surface": "support",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "topic": "authentication",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    result = await task(case)
    env = result["environment_state"]
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "repo_dir" in by_name
    ev = by_name.get("evidence_content")
    assert isinstance(ev, list) and len(ev) >= 2  # seed doc + cited errors.md
    joined = " ".join(str(chunk) for chunk in ev)
    assert "AuthorizationError" in joined


def test_build_event_discord_carries_source_and_grounding() -> None:
    """The discord event must surface source="discord", event_type="discord.message",
    and the same repo grounding as the support surface."""

    case = _case(
        "discord-auth-error",
        "How do I fix AuthorizationError on permissions.check()?",
        "Permission names are case-sensitive",
        {
            "surface": "discord",
            "source": "discord",
            "repository": "TheGreatBonnie/authly",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    ev = build_event(case, "discord")
    assert ev["event_type"] == "discord.message"
    assert ev["source"] == "discord"
    assert ev["question"] == "How do I fix AuthorizationError on permissions.check()?"
    support_payload = ev.get("support") or {}
    assert support_payload.get("repo_dir")
    assert support_payload.get("evidence")
    assert any(
        "troubleshoot-errors" in str(d)
        for d in support_payload.get("related_docs", [])
    )
    assert ev.get("repository") == "TheGreatBonnie/authly"


def test_build_event_slack_carries_source_and_grounding() -> None:
    """The slack event must surface source="slack", event_type="slack.message",
    and the same repo grounding as the support surface."""

    case = _case(
        "slack-auth-error",
        "How do I fix AuthorizationError on permissions.check()?",
        "Permission names are case-sensitive",
        {
            "surface": "slack",
            "source": "slack",
            "repository": "TheGreatBonnie/authly",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    ev = build_event(case, "slack")
    assert ev["event_type"] == "slack.message"
    assert ev["source"] == "slack"
    assert ev["question"] == "How do I fix AuthorizationError on permissions.check()?"
    support_payload = ev.get("support") or {}
    assert support_payload.get("repo_dir")
    assert support_payload.get("evidence")
    assert any(
        "troubleshoot-errors" in str(d)
        for d in support_payload.get("related_docs", [])
    )
    assert ev.get("repository") == "TheGreatBonnie/authly"


def test_build_event_support_source_from_metadata() -> None:
    """The support build_event branch must read source from metadata,
    not hardcode it to 'slack'."""

    discord_case = _case(
        "q1",
        "How does OAuth work?",
        "OAuth uses PKCE",
        {
            "surface": "support",
            "source": "discord",
        },
    )
    slack_case = _case(
        "q2",
        "How does OAuth work?",
        "OAuth uses PKCE",
        {
            "surface": "support",
            "source": "slack",
        },
    )
    default_case = _case(
        "q3",
        "How does OAuth work?",
        "OAuth uses PKCE",
        {"surface": "support"},
    )

    assert build_event(discord_case, "support")["source"] == "discord"
    assert build_event(slack_case, "support")["source"] == "slack"
    assert build_event(default_case, "support")["source"] == "slack"


async def test_build_online_task_env_state_grounds_discord_surface() -> None:
    """The online task must feed evidence + cited-source content into
    <ActualEnvironmentState> for the discord surface, mirroring the support path."""

    from draftly.agents.schemas import ImpactAnalysis

    class Client:
        async def invoke(self, **kwargs):
            return StubGraphResult(
                [
                    StubNode(
                        "impact",
                        [
                            StubAgentResult(
                                ["ImpactAnalysis"],
                                structured_output=ImpactAnalysis(
                                    action="answer",
                                    evidence=["authly/docs/reference/errors.md"],
                                ),
                            )
                        ],
                    )
                ]
            )

    task = build_online_task(Client())
    case = _case(
        "discord-auth-error",
        "AuthorizationError on permissions.check()",
        "Permission names are case-sensitive",
        {
            "surface": "discord",
            "source": "discord",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/how-to/troubleshoot-errors",
                    "topic": "authentication",
                    "url": "authly/docs/how-to/troubleshoot-errors.md",
                }
            ],
        },
    )

    result = await task(case)
    env = result["environment_state"]
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "repo_dir" in by_name
    ev = by_name.get("evidence_content")
    assert isinstance(ev, list) and len(ev) >= 2
    joined = " ".join(str(chunk) for chunk in ev)
    assert "AuthorizationError" in joined


async def test_build_online_task_env_state_grounds_release_surface() -> None:
    """Release runs must feed repo_dir/evidence/evidence_content/changed_files
    into <ActualEnvironmentState> like the issue/support paths, so
    groundedness/correctness can verify claims against the real source instead
    of scoring 0.0 (the release run's judges reported no source material)."""
    from draftly.agents.schemas import ImpactAnalysis

    class Client:
        async def invoke(self, **kwargs):
            return StubGraphResult(
                [
                    StubNode(
                        "impact",
                        [
                            StubAgentResult(
                                ["ImpactAnalysis"],
                                structured_output=ImpactAnalysis(
                                    action="create",
                                    evidence=["authly/docs/reference/errors.md"],
                                ),
                            )
                        ],
                    )
                ]
            )

    task = build_online_task(Client())
    case = _case(
        "breaking-major-release",
        "Release v2.0.0: BREAKING - Removed API key authentication",
        "Changelog entry with Changed (Breaking) and Added sections",
        {
            "surface": "release",
            "event_type": "release.published",
            "repo_dir": str(Path(__file__).resolve().parents[3] / "authly"),
            "evidence": [
                {
                    "id": "docs/reference/errors",
                    "topic": "errors",
                    "url": "authly/docs/reference/errors.md",
                }
            ],
            "changed_files": [
                {"path": "src/authly/client.py", "change": "deprecated api_key"},
            ],
            "tag_name": "v2.0.0",
        },
    )

    result = await task(case)
    env = result["environment_state"]
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "repo_dir" in by_name
    assert "changed_files" in by_name
    ev = by_name.get("evidence_content")
    assert isinstance(ev, list) and len(ev) >= 1
    joined = " ".join(str(chunk) for chunk in ev)
    assert "AuthorizationError" in joined


def test_discord_dataset_matches_live_shape() -> None:
    """discord.json must be a list-wrapped dataset with surface=discord,
    one case, and relaxed threshold matching the support surface."""
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "evaluation"
        / "datasets"
        / "discord.json"
    )
    data = json.loads(path.read_text())
    assert isinstance(data, list) and len(data) == 1, "discord.json must be a 1-element list"
    ds = data[0]
    assert ds["surface"] == "discord"
    assert "required_tools" in ds
    cases = ds["cases"]
    assert len(cases) == 1, "discord.json must cover exactly one case"
    for c in cases:
        meta = c["metadata"]
        assert meta.get("expected_contains_threshold") == 0.45
        assert meta.get("surface") == "discord"
        assert meta.get("source") == "discord"
        assert str(meta.get("repo_dir", "")).endswith("authly")
        assert meta.get("repository")


def test_support_dataset_matches_live_shape() -> None:
    """The live CLI iterates datasets as a list; each entry must be a dict with
    a single case carrying repo metadata + a relaxed threshold like the
    github_issues dataset the support run must mirror."""
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "evaluation"
        / "datasets"
        / "support.json"
    )
    data = json.loads(path.read_text())
    assert isinstance(data, list) and len(data) == 1, "support.json must be a 1-element list"
    ds = data[0]
    assert ds["surface"] == "support"
    assert "required_tools" in ds
    cases = ds["cases"]
    assert len(cases) == 1, "support.json must cover exactly one case"
    meta = cases[0]["metadata"]
    assert meta.get("expected_contains_threshold") == 0.45
    assert str(meta.get("repo_dir", "")).endswith("authly")
    assert meta.get("repository")


def test_release_scenario_ground_truth_matches_v2_0_0_release_notes() -> None:
    """The breaking-major-release scenario must reflect the v2.0.0 state its
    release notes describe so judges can verify authored claims against the
    checkout: api_key removed (scoped_token required), OAuth authorization-code
    exchange present, and RBAC organization scoping implemented."""
    scenario = (
        Path(__file__).resolve().parents[3]
        / "authly-scenarios"
        / "003-api-key-deprecation"
    )
    assert scenario.is_dir(), scenario

    client_src = (scenario / "src" / "authly" / "client.py").read_text()
    assert "api_key" not in client_src
    assert "scoped_token is required" in client_src
    assert "DeprecationWarning" not in client_src

    init_src = (scenario / "src" / "authly" / "__init__.py").read_text()
    assert '__version__ = "2.0.0"' in init_src

    oauth_src = (scenario / "src" / "authly" / "oauth.py").read_text()
    assert "def exchange_code" in oauth_src

    for src in ("roles.py", "permissions.py"):
        module = (scenario / "src" / "authly" / src).read_text()
        assert "organization_id" in module, src

    model_doc = (
        scenario / "docs" / "explanation" / "authorization-model.md"
    ).read_text()
    assert "no effect on authorization" not in model_doc
    assert "organization-scoped" in model_doc


def test_expected_contains_checks_each_case_own_output() -> None:
    evaluator = ExpectedContains()

    hit = evaluator.evaluate(
        SimpleNamespace(
            expected_output="enable retries",
            actual_output="You should enable retries via config.",
        )
    )[0]
    assert hit.test_pass is True

    miss = evaluator.evaluate(
        SimpleNamespace(expected_output="enable retries", actual_output="unrelated answer")
    )[0]
    assert miss.test_pass is False
    assert miss.score == 0.0


def test_judge_rubrics_do_not_penalize_hitl_interrupt() -> None:
    """Interrupted runs (ReviewGate review_policy=always) are a designed
    suspension before final delivery; the groundedness/correctness judges must
    not treat the STATUS.INTERRUPTED / deliver_ran=False signal as a tooling
    failure (the release run scored both metrics 0.0 for a clean interrupt)."""
    from draftly.evaluation.evaluators.correctness import CORRECTNESS_RUBRIC
    from draftly.evaluation.evaluators.groundedness import GROUNDEDNESS_RUBRIC

    for rubric in (GROUNDEDNESS_RUBRIC, CORRECTNESS_RUBRIC):
        assert "INTERRUPTED" in rubric
        assert "human review" in rubric
        assert "deliver_ran" in rubric


def test_expected_contains_respects_case_threshold() -> None:
    evaluator = ExpectedContains()

    # 3-of-6 significant-token overlap = 0.50 coverage: below the 0.60 default.
    expected = "one two three four five six"
    actual = "one two three one two three"

    default = evaluator.evaluate(
        SimpleNamespace(expected_output=expected, actual_output=actual)
    )[0]
    assert default.test_pass is False
    assert default.score == 0.5

    # A case-level threshold lets a paraphrased support/answer surface pass.
    relaxed = evaluator.evaluate(
        SimpleNamespace(
            expected_output=expected,
            actual_output=actual,
            metadata={"expected_contains_threshold": 0.5},
        )
    )[0]
    assert relaxed.test_pass is True


def test_expected_tool_called_flags_missing_tools() -> None:
    evaluator = ExpectedToolCalled()

    hit = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_tools": ["semantic_search"]},
            actual_trajectory=["semantic_search", "code_search"],
        )
    )[0]
    assert hit.test_pass is True

    miss = evaluator.evaluate(
        SimpleNamespace(
            metadata={"expected_tools": ["semantic_search", "code_search"]},
            actual_trajectory=["semantic_search"],
        )
    )[0]
    assert miss.test_pass is False
    assert "code_search" in miss.reason


def test_expected_tool_called_passes_without_expected_tools() -> None:
    out = ExpectedToolCalled().evaluate(
        SimpleNamespace(metadata={}, actual_trajectory=[])
    )[0]
    assert out.test_pass is True


async def test_extract_output_text_falls_back_to_deliver() -> None:
    graph = StubGraphResult([StubNode("deliver", [StubAgentResult([], text="delivered")])])
    assert extract_output_text(graph) == "delivered"


# ---------------------------------------------------------------------------
# Real Strands GraphResult shape: execution_order holds GraphNode (no
# get_agent_results). Regression tests for the live-eval silent-zero bug.
# ---------------------------------------------------------------------------


def test_trajectory_extraction_handles_real_graphnode_execution_order() -> None:
    """extract_trajectories must resolve the NodeResult from GraphNode.result.

    Regression: real ``GraphResult.execution_order`` is a list of ``GraphNode``
    dataclasses which have NO ``get_agent_results()`` — calling it crashed with
    AttributeError, which the Evals worker swallowed into results=0.
    """
    from draftly.evaluation.trajectory import extract_trajectories, flatten_trajectory

    ar = _real_agent_result(tool_names=["semantic_search", "keyword_search"])
    graph = _real_graph_result([("context", ar)])

    trajectories = extract_trajectories(graph)
    # No crash; the context node's tool usage is surfaced from GraphNode.result.
    assert list(trajectories) == ["context"]
    assert flatten_trajectory(trajectories) == ["semantic_search", "keyword_search"]


def test_extract_authored_content_handles_real_graphnode_execution_order() -> None:
    """extract_authored_content must read writer plans via GraphNode.result."""
    import json

    from draftly.evaluation.online import extract_authored_content

    plan_ar = _real_agent_result(
        json.dumps(
            {
                "files": [
                    {"path": "docs/oauth.md", "content": "PKCE is required.", "action": "update"}
                ],
                "summary": "update oauth docs",
            }
        )
    )
    graph = _real_graph_result([("update", plan_ar)])
    assert extract_authored_content(graph) == "PKCE is required."


def test_extract_delivery_summary_handles_real_graphnode_execution_order() -> None:
    """extract_delivery_summary must read the deliver node via GraphNode.result."""
    from draftly.evaluation.online import extract_delivery_summary

    graph = _real_graph_result([("deliver", _real_agent_result("pr://acme/api/8"))])
    assert extract_delivery_summary(graph) == "pr://acme/api/8"


def test_extract_output_text_handles_real_graphnode_execution_order() -> None:
    """extract_output_text must read answer/deliver text via GraphNode.result."""
    from draftly.evaluation.online import extract_output_text

    graph = _real_graph_result([("answer", _real_agent_result("expected answer"))])
    assert extract_output_text(graph) == "expected answer"


def _make_scenario_repo(tmp_path: str) -> str:
    import subprocess
    from pathlib import Path

    root = Path(tmp_path) / "repo"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)

    git("init", "-q", "-b", "feat/001-oauth-login")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "docs").mkdir()
    (root / "docs" / "oauth.md").write_text("# OAuth\nnot yet supported\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    # Working-tree doc change = an UPDATE, not a create.
    (root / "docs" / "oauth.md").write_text("# OAuth\nnow fully supported\n")
    return str(root)


async def test_build_online_task_reconciles_expected_action_from_worktree(
    tmp_path: str,
) -> None:
    """A stale declared expected_action must be overridden by the real diff.

    Regression: ``001-oauth-login`` declares ``expected_action: create``, but the
    scenario's docs already exist and are modified, so the true action is
    ``update``. The online task must rewrite the case's ``expected_action`` from
    the real worktree's ``authoring_action`` so the evaluator scores correctly.
    """
    from strands_evals import Case

    from draftly.evaluation.online import build_online_task

    repo_dir = _make_scenario_repo(tmp_path)
    case = Case(
        name="oauth-auth",
        input="PR: add OAuth",
        expected_output="OAuth must be documented",
        metadata={
            "surface": "pull_request",
            "event_type": "pull_request.merged",
            "repo_dir": repo_dir,
            "expected_action": "create",  # stale/incorrect declaration
        },
    )

    client = FakeClient()
    task = build_online_task(client)
    await task(case)

    assert case.metadata["expected_action"] == "update"


# ---------------------------------------------------------------------------
# Feedback loop evaluators (P1)
# ---------------------------------------------------------------------------


def _feedback_env_state(
    *,
    gap_count: int = 0,
    prioritized_gaps: list[dict] | None = None,
) -> list[dict]:
    return [
        {
            "name": "feedback",
            "state": {
                "gap_count": gap_count,
                "prioritized_gaps": prioritized_gaps or [],
            },
        }
    ]


def _feedback_case(
    name: str,
    expected_gaps: list[dict] | None = None,
    expected_gap_count: int | None = None,
    expect_no_gaps: bool = False,
    env_state: list[dict] | None = None,
) -> SimpleNamespace:
    metadata: dict = {"surface": "feedback"}
    if expected_gaps is not None:
        metadata["expected_gaps"] = expected_gaps
    if expected_gap_count is not None:
        metadata["expected_gap_count"] = expected_gap_count
    if expect_no_gaps:
        metadata["expect_no_gaps"] = True
    return SimpleNamespace(
        metadata=metadata,
        actual_output="",
        actual_environment_state=env_state or _feedback_env_state(),
    )


# -- ExpectedGapDetected --


def test_expected_gap_detected_passes_when_all_topics_found() -> None:
    from draftly.evaluation.runner import ExpectedGapDetected

    case = _feedback_case(
        "g1",
        expected_gaps=[{"topic": "oauth"}, {"topic": "rbac"}],
        env_state=_feedback_env_state(
            gap_count=2,
            prioritized_gaps=[{"topic": "oauth"}, {"topic": "rbac"}],
        ),
    )
    case.actual_output = "gaps: oauth, rbac"
    evaluator = ExpectedGapDetected()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True
    assert result.score == 1.0


def test_expected_gap_detected_fails_when_topic_missing() -> None:
    from draftly.evaluation.runner import ExpectedGapDetected

    case = _feedback_case(
        "g2",
        expected_gaps=[{"topic": "oauth"}, {"topic": "rbac"}],
        env_state=_feedback_env_state(
            gap_count=1,
            prioritized_gaps=[{"topic": "oauth"}],
        ),
    )
    case.actual_output = "gaps: oauth"
    evaluator = ExpectedGapDetected()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is False
    assert result.score == 0.5


def test_expected_gap_detected_skips_when_no_expected_gaps() -> None:
    from draftly.evaluation.runner import ExpectedGapDetected

    case = _feedback_case("g3")
    evaluator = ExpectedGapDetected()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True
    assert result.score == 1.0


# -- ExpectedGapCount --


def test_expected_gap_count_passes_when_match() -> None:
    from draftly.evaluation.runner import ExpectedGapCount

    case = _feedback_case(
        "c1",
        expected_gap_count=2,
        env_state=_feedback_env_state(gap_count=2),
    )
    evaluator = ExpectedGapCount()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True


def test_expected_gap_count_fails_when_mismatch() -> None:
    from draftly.evaluation.runner import ExpectedGapCount

    case = _feedback_case(
        "c2",
        expected_gap_count=3,
        env_state=_feedback_env_state(gap_count=1),
    )
    evaluator = ExpectedGapCount()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is False
    assert result.score == 0.0


def test_expected_gap_count_skips_when_not_declared() -> None:
    from draftly.evaluation.runner import ExpectedGapCount

    case = _feedback_case("c3")
    evaluator = ExpectedGapCount()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True


# -- NoFalsePositiveGap --


def test_no_false_positive_gap_passes_when_zero_gaps() -> None:
    from draftly.evaluation.runner import NoFalsePositiveGap

    case = _feedback_case(
        "fp1",
        expect_no_gaps=True,
        env_state=_feedback_env_state(gap_count=0),
    )
    evaluator = NoFalsePositiveGap()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True


def test_no_false_positive_gap_fails_when_gaps_detected() -> None:
    from draftly.evaluation.runner import NoFalsePositiveGap

    case = _feedback_case(
        "fp2",
        expect_no_gaps=True,
        env_state=_feedback_env_state(gap_count=2),
    )
    evaluator = NoFalsePositiveGap()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is False
    assert result.score == 0.0


def test_no_false_positive_gap_skips_when_not_declared() -> None:
    from draftly.evaluation.runner import NoFalsePositiveGap

    case = _feedback_case("fp3")
    evaluator = NoFalsePositiveGap()
    result = evaluator.evaluate(case)[0]
    assert result.test_pass is True


# -- build_live_evaluators wiring for feedback --


def test_build_live_evaluators_wires_feedback_evaluators() -> None:
    from draftly.evaluation.runner import build_live_evaluators

    cases = [
        _feedback_case(
            "fb1",
            expected_gaps=[{"topic": "oauth"}],
            expected_gap_count=1,
            expect_no_gaps=False,
        )
    ]
    evaluators = build_live_evaluators(cases)
    names = [e.name for e in evaluators]
    assert "expected_gap_detected" in names
    assert "expected_gap_count" in names
    assert "no_false_positive_gap" in names


async def test_build_online_task_feedback_decodes_json_list_input() -> None:
    """Feedback cases (e.g. feedback.json) ship ``questions`` as a JSON-encoded
    list string; the online task must decode it back into a real list so the
    deterministic feedback graph can cluster it. Regression: the graph
    previously iterated the encoded string char-by-char and raised
    ``AttributeError: 'str' object has no attribute 'get'``."""

    class NoopClient:
        async def invoke(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("feedback surface must not invoke the client")

    task = build_online_task(NoopClient())
    case = _case(
        "feedback_json_list_input",
        '[{"topic": "oauth", "question": "How do I set up OAuth?"}, '
        '{"topic": "oauth", "question": "OAuth redirect fails"}, '
        '{"topic": "keyboards", "question": "unrelated"}]',
        "exactly one oauth gap",
        {"surface": "feedback", "gap_threshold": 2},
    )

    result = await task(case)

    feedback = next(e for e in result["environment_state"] if e.name == "feedback")
    assert feedback.state["gap_count"] == 1
    assert feedback.state["prioritized_gaps"][0]["topic"] == "oauth"


def test_build_live_evaluators_skips_feedback_when_not_present() -> None:
    from draftly.evaluation.runner import build_live_evaluators

    cases = [
        SimpleNamespace(
            metadata={"surface": "support"},
        )
    ]
    evaluators = build_live_evaluators(cases)
    names = [e.name for e in evaluators]
    assert "expected_gap_detected" not in names
    assert "expected_gap_count" not in names
    assert "no_false_positive_gap" not in names


# ---------------------------------------------------------------------------
# Content surface (live manifold for the re-enabled content dataset)
# ---------------------------------------------------------------------------


def test_build_event_content_surface_shape() -> None:
    """build_event must render a ``content`` surface as a release-manifold
    event that the content graph's ``_request_from_event`` can consume.

    The content manifold re-uses the ``release`` payload slot, carrying the
    original release event type, title, and declared evidence so groundedness
    judges and the keyword_search eligibility gate see the source material.
    """
    case = _case(
        "grounded_release_variants",
        "Release v2.0.0: BREAKING - removed API key authentication. Migrate to OAuth.",
        "Blog and social variants grounded in the release notes",
        {
            "surface": "content",
            "event_type": "content.manual",
            "source_event_type": "release",
            "repository": "TheGreatBonnie/authly",
            "project_id": "proj_demo",
            "org_id": "org_demo",
            "requested_channels": ["blog", "linkedin", "x"],
            "evidence": [{"id": "release-notes", "topic": "oauth"}],
            "audience": "developers",
            "tone": "practical",
        },
    )

    event = build_event(case, "content")

    assert event["event_type"] == "content.manual"
    assert event["content_relevant"] is True
    assert event["org_id"] == "org_demo"
    assert event["repository"] == "TheGreatBonnie/authly"
    assert event["project_id"] == "proj_demo"
    assert event["requested_channels"] == ["blog", "linkedin", "x"]
    assert event["audience"] == "developers"
    assert event["tone"] == "practical"
    release = event["release"]
    assert release["source_event_type"] == "release"
    assert release["source_evidence"] == [{"id": "release-notes", "topic": "oauth"}]
    assert (
        release["source_title"]
        == "Release v2.0.0: BREAKING - removed API key authentication. Migrate to OAuth."
    )
    assert release["source_summary"] == case.input


def test_build_event_content_unsupported_variant_has_empty_evidence() -> None:
    """An unsupported content variant must ship with empty evidence so the
    groundedness frontier treats it as unsupported (no release notes to ground
    against) while still carrying the same source-event provenance."""
    case = _case(
        "unsupported_variant_is_blocked",
        "Release v9.9.9: telemetry overhaul",
        "The unsupported variant must record authoring feedback",
        {
            "surface": "content",
            "event_type": "content.manual",
            "source_event_type": "release",
            "repository": "TheGreatBonnie/authly",
            "org_id": "org_demo",
            "requested_channels": ["blog", "linkedin", "x"],
            "evidence": [],
        },
    )

    event = build_event(case, "content")

    assert event["release"]["source_evidence"] == []
    assert event["release"]["source_event_type"] == "release"


def test_extract_authored_content_reads_content_writer_nodes() -> None:
    """Content runs author via content_blog (ContentDraftOutput) and
    content_linkedin / content_x (ContentSocialOutput); the titled variants
    must be part of the scored output, not an empty string."""
    from draftly.agents.content.schemas import ContentDraftOutput, ContentSocialOutput

    graph = StubGraphResult(
        [
            StubNode(
                "content_blog",
                [
                    StubAgentResult(
                        [],
                        structured_output=ContentDraftOutput(
                            title="v2.0.0 release",
                            summary="summary",
                            body=(
                                "Migration guide: OAuth authorization-code flow "
                                "replaces API key authentication (BREAKING)."
                            ),
                        ),
                    )
                ],
            ),
            StubNode(
                "content_linkedin",
                [
                    StubAgentResult(
                        [],
                        structured_output=ContentSocialOutput(
                            channel="linkedin",
                            title="v2.0.0",
                            body="The v2.0.0 release removes API key authentication.",
                        ),
                    )
                ],
            ),
            StubNode(
                "content_x",
                [
                    StubAgentResult(
                        [],
                        structured_output=ContentSocialOutput(
                            channel="x",
                            title="v2.0.0",
                            body="v2.0.0 drops API keys: migrate to OAuth with RBAC.",
                        ),
                    )
                ],
            ),
        ]
    )

    text = extract_authored_content(graph)

    assert "v2.0.0" in text
    assert "OAuth authorization-code flow" in text
    assert "removes API key authentication" in text
    assert "drops API keys: migrate to OAuth with RBAC" in text


def test_extract_authored_content_content_only_brief_is_empty() -> None:
    """A content run that produced no authored nodes must not crash and must
    yield an empty string (nothing to verbatim-score)."""
    from draftly.evaluation.online import extract_authored_content

    graph = StubGraphResult(
        [StubNode("content_brief", [StubAgentResult(["keyword_search"])])]
    )

    assert extract_authored_content(graph) == ""


# -- Content review-feedback extraction ------------------------------------


def test_extract_content_review_feedback_reads_blocked_evaluate_node() -> None:
    """A failed content run must surface the evaluate node's blocking issues
    as authoring feedback so the harness judges read feedback, not a draft
    that was never approved."""
    from draftly.evaluation.online import extract_content_review_feedback

    evaluate_ar = _real_agent_result(
        json.dumps(
            {
                "passed": False,
                "variants": [],
                "issues": [
                    "missing evidence references",
                    "claims telemetry exporter behavior absent from evidence",
                ],
            }
        )
    )
    graph = _real_graph_result([("evaluate", evaluate_ar)])

    text = extract_content_review_feedback(graph)

    assert "Authoring feedback:" in text
    assert "missing evidence references" in text
    assert "claims telemetry exporter behavior absent from evidence" in text
    assert "Do not publish content claiming behavior that has no supporting evidence." in text


def test_extract_content_review_feedback_empty_when_passed() -> None:
    from draftly.evaluation.online import extract_content_review_feedback

    evaluate_ar = _real_agent_result(
        json.dumps({"passed": True, "variants": [], "issues": []})
    )

    assert extract_content_review_feedback(_real_graph_result([("evaluate", evaluate_ar)])) == ""


def test_extract_content_review_feedback_empty_for_non_content_evaluate() -> None:
    """The docs/issue ``evaluate`` node emits ``score``/``reasons`` (no
    ``variants``); it must never be mistaken for content authoring feedback."""
    from draftly.evaluation.online import extract_content_review_feedback

    evaluate_ar = _real_agent_result(
        json.dumps({"passed": False, "score": 0.2, "reasons": ["low"], "iteration": 3})
    )

    assert extract_content_review_feedback(_real_graph_result([("evaluate", evaluate_ar)])) == ""


def test_extract_content_review_feedback_empty_without_evaluate_node() -> None:
    from draftly.evaluation.online import extract_content_review_feedback

    graph = StubGraphResult([StubNode("content_brief", [StubAgentResult([])])])

    assert extract_content_review_feedback(graph) == ""


def test_compose_content_output_prefers_review_feedback_when_blocked() -> None:
    """When evaluation blocks a content run, the scored output must be the
    deterministic authoring feedback rather than the unapproved draft."""
    from draftly.evaluation.online import compose_content_output

    graph = StubGraphResult(
        [
            StubNode(
                "content_blog",
                [
                    StubAgentResult(
                        [],
                        structured_output={
                            "title": "telemetry release",
                            "body": "claims telemetry behavior with no support",
                        },
                    )
                ],
            ),
            StubNode(
                "evaluate",
                [
                    StubAgentResult(
                        [],
                        structured_output={
                            "passed": False,
                            "variants": [],
                            "issues": ["claims telemetry behavior absent from evidence"],
                        },
                    )
                ],
            ),
        ]
    )

    text = compose_content_output(graph)

    assert text.startswith("Authoring feedback:")
    assert "claims telemetry behavior absent from evidence" in text
    assert "claims telemetry behavior with no support" not in text


def test_compose_content_output_returns_authored_content_when_passed() -> None:
    from draftly.evaluation.online import compose_content_output

    graph = StubGraphResult(
        [
            StubNode(
                "content_blog",
                [
                    StubAgentResult(
                        [],
                        structured_output={
                            "title": "v2.0.0",
                            "body": "grounded blog content",
                        },
                    )
                ],
            ),
            StubNode(
                "evaluate",
                [
                    StubAgentResult(
                        [],
                        structured_output={"passed": True, "variants": [], "issues": []},
                    )
                ],
            ),
        ]
    )

    text = compose_content_output(graph)

    assert text.startswith("v2.0.0")
    assert "grounded blog content" in text


def test_build_event_content_carries_loadable_evidence_content(tmp_path: str) -> None:
    """The content event must carry the declared evidence FILE CONTENTS (not
    just ids) so the in-graph grounding judge can verify draft claims."""
    from draftly.evaluation.online import build_event

    repo_dir = _make_scenario_repo(tmp_path)
    case = _case(
        "grounded_release_variants",
        "Release v2.0.0",
        "grounded",
        {
            "surface": "content",
            "event_type": "content.manual",
            "source_event_type": "release",
            "repo_dir": repo_dir,
            "evidence": [{"id": "oauth", "url": "docs/oauth.md"}],
            "requested_channels": ["blog"],
        },
    )

    event = build_event(case, "content")

    assert event["release"]["source_evidence_content"] == [
        {"path": "docs/oauth.md", "content": "# OAuth\nnow fully supported\n"}
    ]


async def test_build_online_task_content_surface_routes_and_grounds() -> None:
    """The content surface must be routed to the graph and its authored blog +
    social variants become the scored output, with the declared evidence in
    environment_state for the live groundedness/quality judges."""
    from draftly.agents.content.schemas import ContentDraftOutput, ContentSocialOutput

    class ContentClient:
        def __init__(self):
            self.calls = []

        async def invoke(self, **kwargs):
            self.calls.append(kwargs)
            return StubGraphResult(
                [
                    StubNode(
                        "content_blog",
                        [
                            StubAgentResult(
                                [],
                                structured_output=ContentDraftOutput(
                                    title="v2.0.0",
                                    summary="summary",
                                    body="OAuth migration and RBAC organization scoping.",
                                ),
                            )
                        ],
                    ),
                    StubNode(
                        "content_linkedin",
                        [
                            StubAgentResult(
                                [],
                                structured_output=ContentSocialOutput(
                                    channel="linkedin",
                                    title="v2.0.0",
                                    body="LinkedIn post: OAuth and RBAC in v2.0.0.",
                                ),
                            )
                        ],
                    ),
                    StubNode(
                        "content_x",
                        [StubAgentResult([], text="x-only draft text")],
                    ),
                    StubNode("deliver", [StubAgentResult([], text="approved")]),
                ]
            )

    client = ContentClient()
    task = build_online_task(client)
    case = _case(
        "grounded_release_variants",
        "Release v2.0.0: BREAKING - removed API key authentication. Migrate to OAuth.",
        "Blog and social variants announce the release, grounded in release notes",
        {
            "surface": "content",
            "event_type": "content.manual",
            "source_event_type": "release",
            "repository": "TheGreatBonnie/authly",
            "org_id": "org_123",
            "requested_channels": ["blog", "linkedin", "x"],
            "evidence": [{"id": "release-notes", "topic": "oauth"}],
        },
    )

    result = await task(case)

    assert client.calls[0]["surface"] == "content"
    event = json.loads(client.calls[0]["task"])
    assert event["event_type"] == "content.manual"
    assert event["content_relevant"] is True
    assert event["release"]["source_evidence"] == [
        {"id": "release-notes", "topic": "oauth"}
    ]
    # Blog + LinkedIn bodies must be surfaced for verbatim scoring; the raw
    # content_x text node is not a structured writer output and is skipped.
    assert "OAuth migration and RBAC organization scoping." in result["output"]
    assert "LinkedIn post: OAuth and RBAC in v2.0.0." in result["output"]
    assert "x-only draft text" not in result["output"]  # raw text node is skipped
    env = result["environment_state"]
    by_name = {
        (e["name"] if isinstance(e, dict) else e.name): (
            e.get("state") if isinstance(e, dict) else e.state
        )
        for e in env
    }
    assert "evidence" in by_name
    assert by_name["evidence"] == [{"id": "release-notes", "topic": "oauth"}]


# -- Content dataset shape --

def test_content_dataset_flags_expected_blocked_for_unsupported() -> None:
    """The unsupported case must carry expected_blocked so the
    documentation_quality evaluator skips the structurally-impossible
    deterministic scoring."""
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "evaluation"
        / "datasets"
        / "content.json"
    )
    data = json.loads(path.read_text())
    ds = data[0]
    cases = {c["name"]: c for c in ds["cases"]}
    unsupported = cases["unsupported_variant_is_blocked"]
    grounded = cases["grounded_release_variants"]
    assert unsupported["metadata"].get("expected_blocked") is True
    assert grounded["metadata"].get("expected_blocked") is not True


# -- Feedback dataset shape --

def test_feedback_dataset_matches_live_shape() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "evaluation"
        / "datasets"
        / "feedback.json"
    )
    data = json.loads(path.read_text())
    assert isinstance(data, list) and len(data) == 1, "feedback.json must be a 1-element list"
    ds = data[0]
    assert ds["surface"] == "feedback"
    cases = ds["cases"]
    assert len(cases) == 3, "feedback.json must cover exactly three cases"
    for c in cases:
        meta = c["metadata"]
        assert meta.get("surface") == "feedback"
        assert "gap_threshold" in meta
