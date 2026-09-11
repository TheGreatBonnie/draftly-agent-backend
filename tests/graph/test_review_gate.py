"""Review gate: interrupt at delivery, resume with approval or rejection
(plan §6.9 #2–#4).

SDK behavior verified against strands-agents 1.52.0:
- First run halts with ``Status.INTERRUPTED``; the interrupt is reported in
  ``result.interrupts`` with a deterministic id (uuid5 of node+name).
- Resume passes ``[{"interruptResponse": {"interruptId", "response"}}]``.
- Rejection sets ``cancel_node``, which makes ``invoke_async`` RAISE
  RuntimeError (fail-fast) — not return a FAILED result.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from strands.multiagent.base import MultiAgentResult, NodeResult, Status

from draftly.integrations.strands.graph import build_graph_for_run
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.base import agent_result
from tests.graph.conftest import PR_TASK


def _interrupt_id(result) -> str:
    assert result.status == Status.INTERRUPTED
    assert result.interrupts, "expected the review gate to raise an interrupt"
    return result.interrupts[0].id


async def _run_to_interrupt(model, tools, tmp_sessions, run_id: str, comment_factory=None):
    graph = build_graph_for_run(
        run_id,
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=comment_factory,
    )
    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": run_id, "review_policy": "always"},
    )
    return graph, result


async def test_gate_interrupts_before_delivery(model, tools, tmp_sessions, comment_factory) -> None:
    factory, _ = comment_factory
    _, result = await _run_to_interrupt(
        model, tools, tmp_sessions, "gate-1", comment_factory=factory
    )
    interrupt_id = _interrupt_id(result)

    order = [n.node_id for n in result.execution_order]
    assert "deliver" not in order
    assert "evaluate" in order
    assert interrupt_id.startswith("v1:before_node_call:")


async def test_resume_with_approval_completes_delivery(
    model, tools, tmp_sessions, comment_factory
) -> None:
    factory, commenter = comment_factory
    _, first = await _run_to_interrupt(
        model, tools, tmp_sessions, "gate-2", comment_factory=factory
    )
    interrupt_id = _interrupt_id(first)

    graph = build_graph_for_run(
        "gate-2",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    result = await graph.invoke_async(
        [
            {
                "interruptResponse": {
                    "interruptId": interrupt_id,
                    "response": {"approved": True, "comment": "ship it"},
                }
            }
        ],
        invocation_state={"run_id": "gate-2", "review_policy": "always"},
    )

    assert result.status == Status.COMPLETED
    order = [n.node_id for n in result.execution_order]
    assert order[-1] == "deliver"


async def test_rejection_cancels_node_and_raises(
    model, tools, tmp_sessions, comment_factory
) -> None:
    factory, _ = comment_factory
    _, first = await _run_to_interrupt(
        model, tools, tmp_sessions, "gate-3", comment_factory=factory
    )
    interrupt_id = _interrupt_id(first)

    graph = build_graph_for_run(
        "gate-3",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    with pytest.raises(RuntimeError, match="Rejected by reviewer"):
        await graph.invoke_async(
            [
                {
                    "interruptResponse": {
                        "interruptId": interrupt_id,
                        "response": {"approved": False, "comment": "nope"},
                    }
                }
            ],
            invocation_state={"run_id": "gate-3", "review_policy": "always"},
        )


async def test_policy_never_skips_the_gate(model, tools, tmp_sessions, comment_factory) -> None:
    factory, _ = comment_factory
    graph = build_graph_for_run(
        "gate-4",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        comment_factory=factory,
    )
    result = await graph.invoke_async(
        PR_TASK,
        invocation_state={"run_id": "gate-4", "review_policy": "never"},
    )

    assert result.status == Status.COMPLETED
    assert [n.node_id for n in result.execution_order][-1] == "deliver"


async def test_interrupt_reason_carries_document_content(
    model, tools, tmp_sessions, comment_factory
) -> None:
    """The gate must attach the proposed document (writer output) to the
    interrupt reason so reviewers can see what they are approving."""
    factory, _ = comment_factory
    _, result = await _run_to_interrupt(
        model, tools, tmp_sessions, "gate-5", comment_factory=factory
    )
    _interrupt_id(result)

    reason = result.interrupts[0].reason
    assert isinstance(reason, dict)
    document = reason.get("document")
    assert isinstance(document, dict), "expected document payload in interrupt reason"
    assert document.get("kind") in ("change_plan", "answer")
    files = document.get("files")
    assert isinstance(files, list) and files, "document must carry the planned files"
    assert files[0]["path"] == "docs/widgets.md"
    assert files[0]["content"], "file content must not be empty"


def test_interrupt_reason_includes_changelog() -> None:
    """The gate must attach the changelog entry (changelog node output) to
    the interrupt reason so reviewers can inspect what would be shipped."""
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-changelog"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "create": SimpleNamespace(
                        result=agent_result(
                            {
                                "files": [{"path": "docs/x.md", "content": "x"}],
                                "summary": "Updated widgets guide",
                            }
                        )
                    ),
                    "changelog": SimpleNamespace(
                        result=agent_result(
                            {
                                "version": "0.2.0",
                                "date": "2026-09-10",
                                "raw_markdown": (
                                    "## [0.2.0] - 2026-09-10\n\n### Added\n\n"
                                    "- OAuth code exchange\n"
                                ),
                            }
                        )
                    ),
                }
            )
        ),
        interrupt=lambda _name, *, reason: reasons.append(reason) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    changelog = reasons[0]["changelog"]
    assert changelog == {
        "version": "0.2.0",
        "date": "2026-09-10",
        "raw_markdown": "## [0.2.0] - 2026-09-10\n\n### Added\n\n- OAuth code exchange\n",
    }


def test_risky_policy_reads_classification_from_graph_state() -> None:
    """A resumed graph must not treat every missing invocation field as risky."""
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "risky"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "classify": SimpleNamespace(
                        result=agent_result({"change_type": "routine", "urgency": "low"})
                    )
                }
            )
        ),
        interrupt=lambda *args, **kwargs: pytest.fail("routine work should not pause"),
        cancel_node=None,
    )

    ReviewGate().gate(event)


def test_interrupt_reason_summary_falls_back_without_document() -> None:
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-1"},
        source=SimpleNamespace(state=SimpleNamespace(results={})),
        interrupt=lambda _name, **kwargs: reasons.append(kwargs["reason"]) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    assert reasons[0]["summary"] == "a documentation review is pending"
    assert reasons[0]["summary"] != "run-1"
    assert reasons[0]["document"] is None


def test_interrupt_reason_summary_uses_writer_summary() -> None:
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-1"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "create": SimpleNamespace(
                        result=agent_result(
                            {
                                "files": [{"path": "docs/x.md", "content": "x"}],
                                "summary": "Updated widgets guide",
                            }
                        )
                    )
                }
            )
        ),
        interrupt=lambda _name, **kwargs: reasons.append(kwargs["reason"]) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    assert reasons[0]["summary"] == "Updated widgets guide"


def test_interrupt_reason_summary_uses_content_title() -> None:
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-1"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "content_blog": SimpleNamespace(
                        result=agent_result({"title": "Monthly Update", "body": "..."})
                    )
                }
            )
        ),
        interrupt=lambda _name, **kwargs: reasons.append(kwargs["reason"]) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    assert reasons[0]["summary"] == "Monthly Update"


def test_interrupt_reason_includes_evaluation_result() -> None:
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-1"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "evaluate": SimpleNamespace(
                        result=MultiAgentResult(
                            status=Status.COMPLETED,
                            results={
                                "evaluate": NodeResult(
                                    result=agent_result(
                                        {"passed": True, "score": 0.92, "reasons": ["grounded"]}
                                    )
                                )
                            },
                        )
                    )
                }
            )
        ),
        interrupt=lambda _name, *, reason: reasons.append(reason) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    assert reasons[0]["evaluation"] == {
        "passed": True,
        "score": 0.92,
        "reasons": ["grounded"],
    }


def test_interrupt_reason_includes_classification_and_structured_evidence() -> None:
    reasons: list[dict] = []
    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-evidence"},
        source=SimpleNamespace(
            state=SimpleNamespace(
                results={
                    "classify": SimpleNamespace(
                        result=agent_result({"change_type": "api_change", "urgency": "high"})
                    ),
                    "context": SimpleNamespace(
                        result=agent_result(
                            {"evidence": [{"id": "src/auth/oauth.py:10", "quote": "PKCE"}]}
                        )
                    ),
                }
            )
        ),
        interrupt=lambda _name, *, reason: reasons.append(reason) or {"approved": True},
        cancel_node=None,
    )

    ReviewGate().gate(event)

    assert reasons[0]["classification"] == {"change_type": "api_change", "urgency": "high"}
    assert reasons[0]["evidence"] == [{"id": "src/auth/oauth.py:10", "quote": "PKCE"}]


def test_review_pause_and_decision_are_logged(monkeypatch) -> None:
    """The gate must surface when it pauses a run for review and how the
    reviewer responded — today it imports structlog but logs nothing."""
    import structlog
    from structlog.testing import capture_logs

    import draftly.orchestration.hooks.review_gate as review_gate_module

    event = SimpleNamespace(
        node_id="deliver",
        invocation_state={"review_policy": "always", "run_id": "run-gate-log"},
        source=SimpleNamespace(state=SimpleNamespace(results={})),
        interrupt=lambda _name, *, reason: {"approved": False, "comment": "nope"},
        cancel_node=None,
    )

    with capture_logs() as logs:
        monkeypatch.setattr(
            review_gate_module,
            "logger",
            structlog.get_logger("test.review_gate.logging"),
        )
        ReviewGate().gate(event)

    pauses = [line for line in logs if line.get("event") == "review_gate_pause"]
    assert len(pauses) == 1
    assert pauses[0]["run_id"] == "run-gate-log"
    assert pauses[0]["policy"] == "always"

    decisions = [line for line in logs if line.get("event") == "review_gate_decision"]
    assert len(decisions) == 1
    assert decisions[0]["approved"] is False
    assert decisions[0]["comment"] == "nope"
    assert event.cancel_node == "Rejected by reviewer: nope"
