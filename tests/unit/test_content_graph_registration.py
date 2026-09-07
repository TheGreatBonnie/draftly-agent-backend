from types import SimpleNamespace

from draftly.app.composition.tools import build_tools
from draftly.integrations.strands import graph as strands_graph
from tests.stub_model import StubModel


def test_content_surface_uses_content_graph_builder(monkeypatch):
    calls = []

    def builder(**kwargs):
        calls.append(kwargs)
        return "content-graph"

    monkeypatch.setitem(strands_graph._BUILDERS, "content", builder)
    result = strands_graph.build_graph_for_run(
        "run-1", "content", tools_registry=SimpleNamespace(), model="model"
    )

    assert result == "content-graph"
    assert calls[0]["model"] == "model"


def test_content_graph_builds_a_real_strands_graph():
    graph = strands_graph._BUILDERS["content"](
        session_manager=None,
        tools_registry=build_tools(),
        model=StubModel(),
        content_repository=object(),
    )

    assert graph is not None
    assert graph is not None


def test_build_graph_for_run_forwards_content_repository_only_for_content(monkeypatch):
    """content_repository is injected by the harness, so it must reach the
    content builder exclusively — non-content builders do not accept it and
    would TypeError on the unexpected kwarg."""
    calls = []

    def builder(**kwargs):
        calls.append(kwargs)
        return "graph"

    repo = object()

    monkeypatch.setitem(strands_graph._BUILDERS, "content", builder)
    monkeypatch.setitem(strands_graph._BUILDERS, "pull_request", builder)

    strands_graph.build_graph_for_run(
        "run-1",
        "content",
        tools_registry=SimpleNamespace(),
        model="model",
        content_repository=repo,
    )
    assert calls[0]["content_repository"] is repo

    calls.clear()
    strands_graph.build_graph_for_run(
        "run-1",
        "pull_request",
        tools_registry=SimpleNamespace(),
        model="model",
        content_repository=repo,
    )
    assert "content_repository" not in calls[0]


def test_strands_client_forwards_content_repository_only_for_content(monkeypatch):
    import draftly.integrations.strands.client as client_mod

    captured = []

    def fake_build(run_id, surface, **kwargs):
        captured.append((surface, "content_repository" in kwargs, kwargs.get("content_repository")))
        return "graph"

    monkeypatch.setattr(client_mod, "build_graph_for_run", fake_build)
    repo = object()
    client = client_mod.StrandsClient(model="model", tools=None, content_repository=repo)

    client.graph_for_run("run-1", "content")
    client.graph_for_run("run-1", "release")

    assert captured[0] == ("content", True, repo)
    assert captured[1] == ("release", False, None)


def test_build_evaluation_graph_live_client_carries_content_repository(monkeypatch):
    """The live evaluation graph client must receive the injected
    content_repository so build_graph_for_run can hand it to the content
    builder. RED: currently the parameter does not exist on
    build_evaluation_graph, so this keyword-forwarding call fails."""
    import draftly.integrations.strands.client as client_mod
    from draftly.orchestration.graphs import evaluation_graph as eg

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(client_mod, "StrandsClient", FakeClient)

    eg.build_evaluation_graph(
        tools_registry=object(),
        model=object(),
        judge_model=object(),
        content_repository="content-repo",
    )

    assert captured.get("content_repository") == "content-repo"
