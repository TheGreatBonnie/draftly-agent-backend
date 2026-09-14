"""EvaluatorNode with ContentBlock inputs matching the graph's format."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from strands.agent.agent_result import AgentResult

from draftly.orchestration.nodes.base import parse_node_input
from draftly.orchestration.nodes.changelog_evaluate import ChangelogEvaluatorNode
from draftly.orchestration.nodes.evaluate import EvaluatorNode, _draft_text, compute_quality
from draftly.orchestration.nodes.rubric_grader import (
    DeterministicRubricGrader,
    RubricGrade,
    build_changelog_rubric_grader,
    build_docs_rubric_grader,
)


def _blocks(
    evidence: list[dict] | None = None,
    draft: str = "",
    *,
    source: str = "update",
) -> list[dict]:
    blocks = [
        {"text": "Original Task: task"},
        {"text": "\nInputs from previous nodes:"},
        {"text": "\nFrom research:"},
        {"text": f"  - Agent: {_json(evidence or [])}"},
        {"text": f"\nFrom {source}:"},
        {"text": f"  - WriterAgent: {_json({'draft': draft})}"},
    ]
    return blocks


def _json(data) -> str:
    import json

    return json.dumps(data)


class TestComputeQuality:
    def test_full_grounding(self) -> None:
        score, reasons = compute_quality(
            [{"id": "doc-1", "topic": "neon"}],
            "Neon is serverless postgres. See doc-1 for details.",
        )
        assert score >= 0.7
        assert any("Grounded in" in r for r in reasons)

    def test_empty_evidence_length_only(self) -> None:
        score, reasons = compute_quality([], "x" * 1000)
        assert score < 0.7
        assert reasons

    def test_missing_citations(self) -> None:
        score, _ = compute_quality(
            [{"id": "doc-1", "topic": "neon"}, {"id": "doc-2", "topic": "pg"}],
            "short draft",
        )
        assert score < 0.5

    def test_full_path_evidence_matches_when_draft_links_basename(self) -> None:
        """A draft that references a source doc by its file name (not the
        exact full-id byte string) must still count as citing that evidence."""
        evidence = [
            {
                "id": "docs/how-to/oauth-authorization-url",
                "topic": "authentication",
                "url": "authly/docs/how-to/oauth-authorization-url.md",
            }
        ]
        draft = (
            "OAuth authentication: build an authorization URL, then link the "
            "reader to docs/how-to/oauth-authorization-url.\n"
        ) * 8
        score, reasons = compute_quality(evidence, draft)
        assert score >= 0.9
        assert any("Grounded in" in r for r in reasons)

    def test_evidence_without_extension_still_matches(self) -> None:
        """id without .md suffix is matched when the draft contains the basename."""
        evidence = [
            {
                "id": "docs/topics/authentication",
                "topic": "authentication",
            }
        ]
        draft = ("Documenting authentication for the SDK. authentication " * 10)
        score, _ = compute_quality(evidence, draft)
        assert score >= 0.6

    def test_code_locator_evidence_matches_generated_doc_prose(self) -> None:
        """A real PR run produces evidence with code-locator ids (e.g.
        ``src/authly/oauth.py``) and no doc path the prose echoes verbatim.
        The generated documentation references the feature by name ("OAuth",
        "exchange_code") but never the ``.py`` path. The coverage matcher must
        recognise the evidence as cited via its significant tokens
        (basename ``oauth``) — otherwise coverage floors at 0 on every
        create-from-code scenario and the writer gets no signal."""
        evidence = [
            {"id": "src/authly/oauth.py", "topic": "oauth"},
            {"id": "src/authly/auth.py", "topic": "authentication"},
        ]
        draft = (
            "OAuth authorization-code exchange: build an authorization URL, "
            "exchange the code for an access token, and use OAuth-backed login. "
            "OAuth authentication authorizes users and starts a session. exchange_code "
            "and login_with_oauth provide the primary flow. authentication is handled "
            "through the OAuth provider. "
        ) * 4
        score, reasons = compute_quality(evidence, draft)
        assert score >= 0.8
        assert any("Grounded in" in r for r in reasons)

    def test_topic_is_authenticated_against_prose_not_case(self) -> None:
        """The coverage matcher must be case-insensitive: a code-locator
        basename ``oauth`` must match prose "OAuth" even though the draft
        capitalizes it and never writes the file path."""
        evidence = [{"id": "src/authly/oauth.py", "topic": "oauth"}]
        draft = ("OAuth provides the primary authentication flow. " * 12)
        score, reasons = compute_quality(evidence, draft)
        assert score >= 0.7
        assert any("Grounded in" in r for r in reasons)

    def test_completeness_falls_back_to_id_basename_without_topic(self) -> None:
        """Live EvidenceBundle items frequently lack a ``topic`` field. The
        completeness signal must derive a fallback topic from the id's terminal
        basename (the feature the doc must cover) so coverage still counts."""
        evidence = [{"id": "src/authly/oauth.py"}]
        draft = ("OAuth provides the OAuth flow with exchange_code and login_with_oauth. " * 12)
        score, reasons = compute_quality(evidence, draft)
        assert score >= 0.8
        assert any("Covers" in r for r in reasons)

    def test_failed_completeness_names_missing_topics(self) -> None:
        """When completeness fails the threshold, the reason must tell the
        writer WHICH topics are missing — otherwise the only signal is the
        opaque score and the revision loop flails (the observed death spiral:
        0.65 -> 0.60 -> 0.55 with no actionable direction)."""
        evidence = [
            {"id": "src/authly/oauth.py", "topic": "oauth"},
            {"id": "src/authly/changelog.py", "topic": "changelog"},
            {"id": "src/authly/models.py", "topic": "models"},
        ]
        draft = ("OAuth provides the OAuth flow with exchange_code. " * 12)
        score, reasons = compute_quality(evidence, draft)
        assert score < 0.7
        missing = [r for r in reasons if r.startswith("Missing topics")]
        assert missing, f"expected a Missing-topics reason, got {reasons!r}"
        assert "changelog" in missing[0]
        assert "models" in missing[0]

    def test_missing_topics_reason_omits_already_covered_topics(self) -> None:
        """The missing-topic list must name only the uncovered topics, never
        the ones the draft already handles."""
        evidence = [
            {"id": "src/authly/oauth.py", "topic": "oauth"},
            {"id": "src/authly/auth.py", "topic": "authentication"},
            {"id": "src/authly/models.py", "topic": "models"},
        ]
        draft = ("OAuth authentication is handled through the OAuth provider. " * 12)
        score, reasons = compute_quality(evidence, draft)
        missing = [r for r in reasons if r.startswith("Missing topics")]
        assert missing
        assert "authentication" not in missing[0]
        assert "oauth" not in missing[0]
        assert "models" in missing[0]

    def test_draft_text_includes_plan_prose_not_just_file_bodies(self) -> None:
        """A DocChangePlan that cites source ids in its summary/rationale must
        give the coverage scorer that text — file bodies rarely echo a source
        id, so scoring only them zeroes citation coverage on good plans."""
        payload = {
            "files": [
                {"path": "docs/reference/api.md", "content": "# API\nAuthly endpoints."}
            ],
            "summary": (
                "Document the OAuth authorization-code exchange backed by "
                "src/authly/oauth.py:36-72."
            ),
            "commit_message": "docs(reference): OAuth exchange endpoint",
            "rationale": "PR adds OAuthClient.exchange_code() and login_with_oauth().",
        }
        text = _draft_text(payload)
        assert "src/authly/oauth.py:36-72" in text
        assert "OAuthClient.exchange_code()" in text
        assert "# API" in text  # file bodies still included


class TestEvaluatorNode:
    @pytest.mark.asyncio
    async def test_unusable_evidence_waives_file_scoped_plan(self) -> None:
        """Evidence that carries NO usable signals (no id/url/topic —
        excerpt-only items) is indistinguishable from a storage artifact; a
        file-scoped plan must not loop forever on a gate it cannot score."""
        evidence = [
            {"content": "some retrieved excerpt with no structured id"},
            {"excerpt": "another opaque chunk"},
        ]
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": f"  - ResearchAgent: {_json({'items': evidence})}"},
            {"text": "\nFrom update:"},
            {
                "text": "  - WriterAgent: "
                + _json(
                    {
                        "summary": "OAuth exchange documentation.",
                        "files": [{"path": "docs/api.md", "content": "# API\n" * 300}],
                    }
                )
            },
        ]
        result = await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(blocks)
        data = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert data["passed"] is True
        assert any("waived" in r.lower() for r in data["reasons"])

    @pytest.mark.asyncio
    async def test_passing_draft(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = (
            "Neon is a serverless Postgres platform. doc-1 doc-1 doc-1 doc-1 doc-1 doc-1 neon " * 12
        )
        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(_blocks(evidence, draft))
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = node_result.message["content"][0]["text"]

        data = json.loads(payload)
        assert set(data) == {"passed", "score", "reasons", "iteration", "escalated"}
        assert data["passed"] is True
        assert data["score"] >= 0.7
        assert data["iteration"] == 1

    @pytest.mark.asyncio
    async def test_failing_draft_then_iteration_cap(self) -> None:
        node = EvaluatorNode(
            max_iterations=2, rubric_grader=_NoopRubricGrader()
        )
        blocks = _blocks([], "tiny")

        first = await node.invoke_async(blocks)
        first_result = first.results["evaluate"].result
        assert isinstance(first_result, AgentResult)
        first_data = json.loads(first_result.message["content"][0]["text"])
        assert first_data["passed"] is False
        assert first_data["iteration"] == 1
        assert first_data.get("escalated") is False

        second = await node.invoke_async(blocks)
        second_result = second.results["evaluate"].result
        assert isinstance(second_result, AgentResult)
        second_data = json.loads(second_result.message["content"][0]["text"])
        # Max iterations with an unmet threshold escalates to human review so
        # the graph reaches the ReviewGate instead of burning the node budget.
        assert second_data["iteration"] == 2
        assert second_data["passed"] is True
        assert second_data["escalated"] is True
        assert any("escalated" in r.lower() for r in second_data["reasons"])

    @pytest.mark.asyncio
    async def test_passing_draft_never_escalates(self) -> None:
        """A draft that passes on its first try must not be escalated."""
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = (
            "Neon is a serverless Postgres platform. doc-1 doc-1 doc-1 doc-1 doc-1 doc-1 neon " * 12
        )
        result = await EvaluatorNode(
            max_iterations=3, rubric_grader=_NoopRubricGrader()
        ).invoke_async(_blocks(evidence, draft))
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["passed"] is True
        assert payload.get("escalated") is False

    @pytest.mark.asyncio
    async def test_change_plan_files_are_scored_as_the_draft(self) -> None:
        evidence = [{"id": "docs/auth.md", "topic": "authentication"}]
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": f"  - ResearchAgent: {_json({'items': evidence})}"},
            {"text": "\nFrom update:"},
            {
                "text": (
                    "  - WriterAgent: "
                    + _json(
                        {
                            "files": [
                                {
                                    "path": "docs/auth.md",
                                    "content": "Authentication docs/auth.md " * 80,
                                }
                            ]
                        }
                    )
                )
            },
        ]

        result = await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(blocks)
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["passed"] is True
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_evidence_bundle_items_are_scored(self) -> None:
        evidence = [{"id": "docs/auth.md", "topic": "authentication"}]
        result = await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(
            _blocks(
                {"items": evidence},
                "Authentication docs/auth.md " * 80,
            )
        )
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_draft_from_answer_node(self) -> None:
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = "Answer text neon doc-1 " * 30
        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(_blocks(evidence, draft, source="answer"))
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = json.loads(node_result.message["content"][0]["text"])
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_plain_text_research_degrades_safely(self) -> None:
        """Non-JSON research output → evidence=[]; scoring still runs."""
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": "  - ResearchAgent: some plain text summary"},
            {"text": "\nFrom update:"},
            {"text": "  - WriterAgent: " + _json({"draft": "tiny"})},
        ]
        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(blocks)
        node_result = result.results["evaluate"].result
        assert isinstance(node_result, AgentResult)
        payload = json.loads(node_result.message["content"][0]["text"])
        assert payload["passed"] is False
        assert payload["score"] < 0.7

    @pytest.mark.asyncio
    async def test_plan_summary_grounds_the_gate(self) -> None:
        """A plan citing its source ids in prose passes the coverage gate even
        when the file bodies themselves never echo the id."""
        evidence = [{"id": "src/authly/oauth.py:36-72"}]
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": f"  - ResearchAgent: {_json(evidence)}"},
            {"text": "\nFrom update:"},
            {
                "text": (
                    "  - WriterAgent: "
                    + _json(
                        {
                            "summary": (
                                "Document the new OAuth authorization-code exchange "
                                "introduced by src/authly/oauth.py:36-72 and the "
                                "login_with_oauth flow. Replace every FAQ claim that "
                                "callback handling and PKCE are unsupported. "
                            )
                            * 6,
                            "files": [
                                {
                                    "path": "docs/reference/api.md",
                                    "content": "# API reference\nAuthly methods.",
                                },
                                {
                                    "path": "docs/how-to/troubleshoot-errors.md",
                                    "content": "# Troubleshooting\nOAuth errors.",
                                },
                            ],
                        }
                    )
                )
            },
        ]

        result = await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(blocks)
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["passed"] is True
        assert payload["score"] >= 0.7

    @pytest.mark.asyncio
    async def test_no_evidence_with_file_plan_waives_to_pass(self) -> None:
        """With zero usable evidence but a file-scoped plan, evaluation must
        proceed to the human ReviewGate instead of forever revising a gate it
        can never score — the observed death spiral."""
        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": "  - ResearchAgent: some plain text summary"},
            {"text": "\nFrom context:"},
            {"text": "  - Agent: also plain text"},
            {"text": "\nFrom update:"},
            {
                "text": (
                    "  - WriterAgent: "
                    + _json(
                        {
                            "summary": "OAuth exchange documentation.",
                            "files": [
                                {"path": "docs/api.md", "content": "# API\n" * 300}
                            ],
                        }
                    )
                )
            },
        ]

        result = await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(blocks)
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert payload["passed"] is True
        assert any("No structured evidence" in r for r in payload["reasons"])

    @pytest.mark.asyncio
    async def test_no_evidence_waiver_verdict_is_logged(self, monkeypatch) -> None:
        """The evaluator's decision must be observable: the run died looping on
        an invisible score, and nothing in the app logs said why or whether an
        empty-evidence waiver fired."""
        import structlog
        from structlog.testing import capture_logs

        import draftly.orchestration.nodes.evaluate as evaluate_module

        blocks = [
            {"text": "Original Task: task"},
            {"text": "\nInputs from previous nodes:"},
            {"text": "\nFrom research:"},
            {"text": "  - ResearchAgent: plain text"},
            {"text": "\nFrom update:"},
            {
                "text": "  - WriterAgent: "
                + _json(
                    {
                        "summary": "OAuth exchange documentation.",
                        "files": [{"path": "docs/api.md", "content": "# API\n" * 300}],
                    }
                )
            },
        ]

        with capture_logs() as logs:
            monkeypatch.setattr(
                evaluate_module,
                "logger",
                structlog.get_logger("test.evaluate.verdict"),
            )
            await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(blocks)

        verdict = [line for line in logs if line.get("event") == "evaluate_verdict"]
        assert len(verdict) == 1
        assert verdict[0]["passed"] is True
        assert verdict[0]["waived"] is True
        assert verdict[0]["evidence_count"] == 0
        assert any("No structured evidence" in r for r in verdict[0]["reasons"])

    @pytest.mark.asyncio
    async def test_failed_verdict_is_logged_without_waiver(self, monkeypatch) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.orchestration.nodes.evaluate as evaluate_module

        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(_blocks([], "tiny"))
        assert json.loads(result.results["evaluate"].result.message["content"][0]["text"])[
            "passed"
        ] is False

        with capture_logs() as logs:
            monkeypatch.setattr(
                evaluate_module,
                "logger",
                structlog.get_logger("test.evaluate.failed"),
            )
            await EvaluatorNode(rubric_grader=_NoopRubricGrader()).invoke_async(_blocks([], "tiny"))

        verdict = [line for line in logs if line.get("event") == "evaluate_verdict"]
        assert len(verdict) == 1
        assert verdict[0]["passed"] is False
        assert verdict[0]["waived"] is False

    @pytest.mark.asyncio
    async def test_parse_node_input_shares_format(self) -> None:
        blocks = _blocks([{"id": "doc-1"}], "draft text")
        deps = parse_node_input(blocks)
        assert "research" in deps
        assert "update" in deps
        assert deps["update"]["draft"] == "draft text"


def test_research_evidence_coerces_evidence_item_models() -> None:
    from draftly.agents.schemas import EvidenceItem
    from draftly.orchestration.nodes.evaluate import _research_evidence

    items = [EvidenceItem(id="src/oauth.py", topic="OAuth")]
    evidence = _research_evidence({"evidence": items})

    assert evidence == [
        {"id": "src/oauth.py", "topic": "OAuth", "url": "", "excerpt": ""}
    ]


# ---------------------------------------------------------------------------
# Option C: rubric grader (adjunct to the deterministic gate)
# ---------------------------------------------------------------------------


class _FakeRubricGrader:
    """Deterministic fake that returns configurable reasons for testing."""

    def __init__(self, *, reasons: list[str] | None = None) -> None:
        self._reasons = reasons or ["Missing topics: oauth, models"]
        self.calls: list[dict] = []

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        self.calls.append({"draft": draft, "evidence": evidence})
        return RubricGrade(score=0.5, passed=False, reasons=list(self._reasons))


class _FailingRubricGrader:
    def __init__(self) -> None:
        self.called = False

    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        self.called = True
        raise RuntimeError("LLM unavailable")


class _NoopRubricGrader:
    async def grade(self, *, draft: str, evidence: list[dict]) -> RubricGrade:
        return RubricGrade()


class _FakeDraftsRepo:
    def __init__(self, revisions: list[dict]) -> None:
        self.revisions = revisions
        self.calls: list[str] = []

    async def get_latest(self, *, run_id: str) -> list:
        self.calls.append(run_id)
        return [
            SimpleNamespace(path=r["path"], action=r["action"], content=r["content"])
            for r in self.revisions
        ]


class TestEvaluatorDraftStore:
    @pytest.mark.asyncio
    async def test_sealed_generation_marks_files_and_scores_store_content(self) -> None:
        """With a drafts repo injected, has_drafts/files_present come from the
        sealed store and the scored draft includes its assembled content."""
        evidence = [{"id": "doc-1", "topic": "neon"}]
        repo = _FakeDraftsRepo(
            [
                {
                    "path": "docs/neon.md",
                    "action": "update",
                    "content": ("neon serverless postgres doc-1 " * 10),
                }
            ]
        )
        node = EvaluatorNode(drafts_repo=repo, rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(
            _blocks(evidence, "", source="update"),
            invocation_state={"run_id": "run-1"},
        )
        data = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert data["passed"] is True
        assert data["has_drafts"] is True
        assert repo.calls == ["run-1"]

    @pytest.mark.asyncio
    async def test_unsealed_generation_reports_has_drafts_false(self) -> None:
        """No sealed revision → has_drafts False (delivery gate blocks)."""
        repo = _FakeDraftsRepo([])
        node = EvaluatorNode(drafts_repo=repo, rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(
            _blocks([{"id": "doc-1", "topic": "neon"}], "", source="update"),
            invocation_state={"run_id": "run-1"},
        )
        data = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert data["has_drafts"] is False

    @pytest.mark.asyncio
    async def test_without_draft_store_keeps_legacy_key_set(self) -> None:
        """drafts_repo=None keeps exactly the legacy result key set."""
        evidence = [{"id": "doc-1", "topic": "neon"}]
        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(
            _blocks(evidence, ("neon serverless postgres doc-1 " * 10), source="update")
        )
        data = json.loads(result.results["evaluate"].result.message["content"][0]["text"])
        assert set(data) == {"passed", "score", "reasons", "iteration", "escalated"}
        assert data["passed"] is True


class TestRubricGraderRequired:
    def test_constructor_requires_a_grader(self) -> None:
        """The rubric grader is a mandatory part of the evaluator contract:
        a node constructed without one is a TypeError, not a silent skip."""
        with pytest.raises(TypeError):
            EvaluatorNode()
        with pytest.raises(TypeError):
            EvaluatorNode(rubric_grader=None)


class TestRubricGrader:
    @pytest.mark.asyncio
    async def test_grader_reasons_enriched_in_payload(self) -> None:
        """When a rubric grader is configured and the draft fails, the
        LLM's specific reasons appear in the payload with a ``[rubric]``
        prefix — the writer's revision pass consumes them."""
        grader = _FakeRubricGrader(reasons=["Missing topics: oauth, models"])
        node = EvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_blocks([], "tiny"))
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])

        assert payload["passed"] is False
        rubric_reasons = [r for r in payload["reasons"] if r.startswith("[rubric]")]
        assert rubric_reasons
        assert "Missing topics: oauth, models" in rubric_reasons[0]
        assert len(grader.calls) == 1

    @pytest.mark.asyncio
    async def test_grader_not_called_when_draft_passes(self) -> None:
        """The rubric grader is an adjunct for failures only; passing drafts
        skip the LLM call to keep the hot path cheap."""
        grader = _FakeRubricGrader()
        evidence = [{"id": "doc-1", "topic": "neon"}]
        draft = ("Neon is a serverless Postgres platform. doc-1 " * 15)
        node = EvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_blocks(evidence, draft))
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])

        assert payload["passed"] is True
        assert len(grader.calls) == 0

    @pytest.mark.asyncio
    async def test_grader_failure_does_not_crash(self) -> None:
        """If the LLM grader raises (timeout, auth, etc.), the evaluator
        still returns its deterministic result gracefully."""
        grader = _FailingRubricGrader()
        node = EvaluatorNode(rubric_grader=grader)
        result = await node.invoke_async(_blocks([], "tiny"))
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])

        assert payload["passed"] is False
        assert grader.called
        rubric_reasons = [r for r in payload["reasons"] if r.startswith("[rubric]")]
        assert rubric_reasons == []

    @pytest.mark.asyncio
    async def test_noop_grader_means_no_rubric_reasons(self) -> None:
        """A no-op grader produces no rubric reasons in the payload, mirroring
        the pre-grader deterministic output exactly."""
        node = EvaluatorNode(rubric_grader=_NoopRubricGrader())
        result = await node.invoke_async(_blocks([], "tiny"))
        payload = json.loads(result.results["evaluate"].result.message["content"][0]["text"])

        rubric_reasons = [r for r in payload["reasons"] if r.startswith("[rubric]")]
        assert rubric_reasons == []


def _task_block() -> list[dict]:
    return [{"text": "Original Task: task"}]


@pytest.mark.asyncio
async def test_deterministic_grader_returns_empty_grade():
    grade = await DeterministicRubricGrader().grade(draft="draft", evidence=[])
    assert grade == RubricGrade()


@pytest.mark.asyncio
async def test_build_docs_rubric_grader_returns_noop_when_model_none():
    grader = build_docs_rubric_grader(None, rubric="the rubric")
    assert isinstance(grader, DeterministicRubricGrader)
    assert await grader.grade(draft="x", evidence=[]) == RubricGrade()


@pytest.mark.asyncio
async def test_build_changelog_grader_returns_noop_when_model_none():
    grader = build_changelog_rubric_grader(None)
    assert isinstance(grader, DeterministicRubricGrader)


@pytest.mark.asyncio
async def test_evaluator_node_completes_with_noop_grader():
    node = EvaluatorNode(rubric_grader=DeterministicRubricGrader())
    result = await node.invoke_async(task=_task_block())
    assert result.status.name == "COMPLETED"


@pytest.mark.asyncio
async def test_changelog_node_completes_with_noop_grader():
    node = ChangelogEvaluatorNode(rubric_grader=DeterministicRubricGrader())
    result = await node.invoke_async(task=_task_block())
    assert result.status.name == "COMPLETED"
