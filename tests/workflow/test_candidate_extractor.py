"""Candidate extractor tests."""

from types import SimpleNamespace

from draftly.workflows.post_run.candidate_extractor import (
    extract_candidates,
    record_post_run_memory,
)
from draftly.workflows.state import WorkflowState


class FakeResult:
    status = "completed"
    interrupts = ()
    execution_order: list = []


def make_state(event=None):
    return WorkflowState(run_id="run-1", event=event or {})


def test_doc_relations_extracted_from_result_payload():
    state = make_state({"project_id": "org1", "source": "github"})
    state.result = FakeResult()
    object.__setattr__(state.result, "changed_files", ["auth/token_service.py"])
    object.__setattr__(state.result, "docs_touched", ["docs/auth/tokens.md"])

    candidates = extract_candidates(state, "github_pr")
    rels = [c for c in candidates if c.candidate_type == "doc_relation"]
    assert len(rels) == 1
    assert rels[0].payload["source_key"] == "auth/token_service.py"
    assert rels[0].payload["target_key"] == "docs/auth/tokens.md"


def test_facts_extracted_with_evidence():
    state = make_state({"project_id": "org1"})
    state.result = FakeResult()
    object.__setattr__(
        state.result,
        "detected_facts",
        [{"content": "tokens expire in 1h", "evidence": ["a.py"], "confidence": 0.9}],
    )
    candidates = extract_candidates(state, "github_pr")
    facts = [c for c in candidates if c.candidate_type == "fact"]
    assert len(facts) == 1
    assert facts[0].evidence == ["a.py"]
    assert facts[0].confidence == 0.9


def test_no_result_means_no_candidates():
    state = make_state({})
    assert extract_candidates(state, "slack_support") == []


def test_graph_result_outputs_are_extracted_for_github_pr():
    state = make_state(
        {
            "project_id": "org1",
            "event_id": "run-1",
            "pull_request": {"changed_files": [{"path": "src/auth.py"}]},
        }
    )
    state.result = SimpleNamespace(
        execution_order=[
            SimpleNamespace(
                node_id="update",
                result=SimpleNamespace(
                    result=SimpleNamespace(
                        results={
                            "update": SimpleNamespace(
                                result=SimpleNamespace(
                                    structured_output=SimpleNamespace(
                                        model_dump=lambda: {
                                            "files": [
                                                {"path": "docs/auth.md", "content": "# Auth"}
                                            ]
                                        }
                                    )
                                )
                            )
                        }
                    )
                ),
            )
        ]
    )

    candidates = extract_candidates(state, "pull_request")

    assert candidates[0].candidate_type == "doc_relation"
    assert candidates[0].payload["source_key"] == "src/auth.py"
    assert candidates[0].payload["target_key"] == "docs/auth.md"


async def test_post_run_memory_preserves_run_and_graph_provenance():
    class Episodes:
        def __init__(self):
            self.fields = None

        async def record_episode(self, **fields):
            self.fields = fields

    class Candidates:
        async def enqueue(self, candidate):
            pass

    state = make_state({"project_id": "org1", "event_id": "run-1", "title": "Auth PR"})
    state.result = SimpleNamespace(
        execution_order=[
            SimpleNamespace(node_id="classify"),
            SimpleNamespace(
                node_id="deliver",
                result=SimpleNamespace(
                    message={"content": [{"toolUse": {"name": "create_pull_request"}}]}
                ),
            ),
        ]
    )
    episodes = Episodes()

    await record_post_run_memory(
        SimpleNamespace(episodic=episodes, candidates=Candidates()),
        state,
        "pull_request",
    )

    assert episodes.fields["agent_run_id"] == "run-1"
    assert episodes.fields["actions_taken"] == ["classify", "deliver"]
    assert episodes.fields["tools_used"] == ["create_pull_request"]


async def test_post_run_memory_captured_is_logged(monkeypatch):
    import structlog
    from structlog.testing import capture_logs

    import draftly.workflows.post_run.candidate_extractor as ce

    class Episodes:
        def __init__(self):
            self.fields = None

        async def record_episode(self, **fields):
            self.fields = fields

    class Candidates:
        def __init__(self):
            self.enqueued = []

        async def enqueue(self, candidate):
            self.enqueued.append(candidate)

    state = make_state({"project_id": "org1", "event_id": "run-1"})
    state.result = FakeResult()
    object.__setattr__(state.result, "changed_files", ["auth/token_service.py"])
    object.__setattr__(state.result, "docs_touched", ["docs/auth/tokens.md"])

    with capture_logs() as logs:
        monkeypatch.setattr(
            ce, "logger", structlog.get_logger("test.post_run_memory_captured")
        )
        await ce.record_post_run_memory(
            SimpleNamespace(episodic=Episodes(), candidates=Candidates()),
            state,
            "github_pr",
        )

    markers = [line for line in logs if line.get("event") == "post_run_memory_captured"]
    assert len(markers) == 1
    assert markers[0]["run_id"] == "run-1"
    assert markers[0]["surface"] == "github_pr"
    assert markers[0]["episode_recorded"] is True
    assert markers[0]["candidate_count"] == 1
