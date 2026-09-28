"""plan_tasks/tasks_from_impact fallback contract tests."""

from __future__ import annotations

from structlog.testing import capture_logs

from draftly.agents.documentation.planning import (
    plan_tasks,
    resolve_task_evidence,
    task_id_for,
    tasks_from_impact,
)
from draftly.agents.schemas import DocumentationTask, EvidenceBundle, EvidenceItem, ImpactAnalysis


def _impact(action: str = "update", paths: list[str] | None = None) -> ImpactAnalysis:
    return ImpactAnalysis(
        action=action, affected_documents=paths or ["docs/a.md"], rationale="behavior changed"
    )


def test_tasks_from_impact_expands_paths_with_scoped_evidence() -> None:
    evidence = EvidenceBundle(
        items=[EvidenceItem(id="docs/a.md", topic="widgets")], summary="s"
    )
    tasks = tasks_from_impact(_impact(paths=["docs/a.md", "docs/b.md"]), evidence)
    assert [t.path for t in tasks] == ["docs/a.md", "docs/b.md"]
    assert tasks[0].evidence[0].id == "docs/a.md"
    assert tasks[1].evidence == []
    assert all(t.id == t.path for t in tasks)
    assert all(t.action == "update" for t in tasks)


def test_tasks_from_impact_defaults_action_for_unknown_action() -> None:
    tasks = tasks_from_impact(_impact(action="none"), None)
    assert all(t.action == "update" for t in tasks)


def test_plan_tasks_prefers_llm_tasks_over_fallback() -> None:
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=[DocumentationTask(id="t-hand", path="docs/b.md", action="create")],
    )
    tasks = plan_tasks(impact, None)
    assert [t.id for t in tasks] == ["t-hand"]


def test_plan_tasks_falls_back_when_llm_emitted_none() -> None:
    tasks = plan_tasks(_impact(paths=["docs/a.md"]), None)
    assert [t.id for t in tasks] == ["docs/a.md"]


def test_task_id_for_is_the_path() -> None:
    assert task_id_for("docs/guide.md") == "docs/guide.md"


def _tasks_only(*paths: str) -> list[DocumentationTask]:
    return [DocumentationTask(id=path, path=path) for path in paths]


def test_resolver_prefers_task_evidence_over_all_sources() -> None:
    """Source 1 wins even when every other source has a match."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=[
            DocumentationTask(
                id="docs/a.md",
                path="docs/a.md",
                evidence=[EvidenceItem(id="docs/a.md", topic="from-tasks")],
            )
        ],
    )
    deps = {
        "context": {"items": [{"id": "docs/a.md", "topic": "from-context"}]},
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.topic for item in task.evidence] == ["from-tasks"]


def test_resolver_reads_context_bundle_when_tasks_omit_evidence() -> None:
    """The production case: LLM tasks carry no evidence, context supplies it."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=_tasks_only("docs/a.md"),
    )
    deps = {"context": {"items": [{"id": "docs/a.md", "topic": "widgets"}]}}
    [task] = resolve_task_evidence(impact, deps)
    assert [item.id for item in task.evidence] == ["docs/a.md"]


def test_resolver_falls_back_to_research_when_context_is_empty() -> None:
    """Legacy precedence, evaluate.py:378-381: context first, research second."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=_tasks_only("docs/a.md"),
    )
    deps = {
        "context": {"items": []},
        "research": {"items": [{"id": "docs/a.md", "topic": "widgets"}]},
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.id for item in task.evidence] == ["docs/a.md"]


def test_resolver_prefers_context_over_research() -> None:
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=_tasks_only("docs/a.md"),
    )
    deps = {
        "context": {"items": [{"id": "docs/a.md", "topic": "from-context"}]},
        "research": {"items": [{"id": "docs/a.md", "topic": "from-research"}]},
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.topic for item in task.evidence] == ["from-context"]


def test_resolver_scopes_impact_prose_evidence_by_doc_match() -> None:
    """Source 2: 'Doc match: <path>' is the only page link in the prose."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md", "docs/b.md"],
        tasks=_tasks_only("docs/a.md", "docs/b.md"),
        evidence=[
            "src/api/users.py:45-67 - added rate limiting\n"
            "Doc match: docs/a.md (score: 0.9)"
        ],
    )
    tasks = resolve_task_evidence(impact, {})
    assert [item.id for item in tasks[0].evidence] == ["docs/a.md"]
    assert "rate limiting" in tasks[0].evidence[0].excerpt
    assert tasks[1].evidence == []


def test_resolver_matches_remaining_pages_by_topic_affinity() -> None:
    """Source 4: no exact id, but the topic overlaps the page name."""
    path = "docs/guides/oauth-authorization-url.md"
    impact = ImpactAnalysis(
        action="update",
        affected_documents=[path],
        tasks=_tasks_only(path),
    )
    deps = {
        "context": {
            "items": [
                {
                    "id": "https://docs.example.com/authorize",
                    "topic": "oauth authorization url guide",
                },
                {"id": "https://docs.example.com/unrelated", "topic": "billing"},
            ]
        }
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.id for item in task.evidence] == [
        "https://docs.example.com/authorize"
    ]


def test_resolver_topic_affinity_caps_at_three_items() -> None:
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/guides/oauth.md"],
        tasks=_tasks_only("docs/guides/oauth.md"),
    )
    deps = {
        "context": {
            "items": [
                {"id": f"https://example.com/{n}", "topic": "oauth guide"} for n in range(6)
            ]
        }
    }
    [task] = resolve_task_evidence(impact, deps)
    assert len(task.evidence) == 3


def test_resolver_matches_page_by_affinity_over_url_and_excerpt() -> None:
    """Source 4 must read the fields the context agent actually fills.

    The agent gathers evidence through ``github_read_file``, so it emits ``url``
    and ``excerpt`` and leaves ``id`` and ``topic`` blank. Run c6d18ea0
    escalated 9/9 pages because this source tokenised only the blank pair.
    """
    path = "docs/how-to/oauth-login-flow.md"
    impact = ImpactAnalysis(
        action="update",
        affected_documents=[path],
        tasks=_tasks_only(path),
    )
    deps = {
        "context": {
            "items": [
                {
                    "url": "https://github.com/acme/authly/blob/main/src/oauth.rs",
                    "excerpt": "the login flow starts the authorization code grant",
                },
                {
                    "url": "https://github.com/acme/authly/blob/main/src/billing.rs",
                    "excerpt": "invoice totals and payment methods",
                },
            ]
        }
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.url for item in task.evidence] == [
        "https://github.com/acme/authly/blob/main/src/oauth.rs"
    ]


def test_resolver_matches_page_across_singular_plural_tokens() -> None:
    """A trailing-s difference must not zero the score.

    ``src/error.rs`` and ``docs/reference/errors.md`` are the same subject;
    exact token equality called them unrelated.
    """
    path = "docs/reference/errors.md"
    impact = ImpactAnalysis(
        action="update",
        affected_documents=[path],
        tasks=_tasks_only(path),
    )
    deps = {
        "context": {
            "items": [
                {
                    "url": "https://github.com/acme/authly/blob/main/src/error.rs",
                    "excerpt": "error variants returned by the API",
                },
                {
                    "url": "https://github.com/acme/authly/blob/main/src/theme.rs",
                    "excerpt": "colour palette for the terminal",
                },
            ]
        }
    }
    [task] = resolve_task_evidence(impact, deps)
    assert [item.url for item in task.evidence] == [
        "https://github.com/acme/authly/blob/main/src/error.rs"
    ]


def test_resolver_logs_why_pages_could_not_be_resolved() -> None:
    """An empty-evidence warning must say which source was empty.

    ``page_evidence_empty`` reported only the failing paths, so diagnosing run
    c6d18ea0 meant reconstructing the resolver's inputs from a 1214-line log.
    The event must carry the dependency keys, the item count it read, and how
    many pages each source rescued.
    """
    unresolved = "docs/explanation/faq.md"
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/how-to/oauth-login-flow.md", unresolved],
        tasks=_tasks_only("docs/how-to/oauth-login-flow.md", unresolved),
    )
    deps = {
        "context": {
            "items": [
                {
                    "url": "https://github.com/acme/authly/blob/main/src/oauth.rs",
                    "excerpt": "authorization code grant",
                }
            ]
        },
        "research": "## Research Summary: PR #67",
    }

    with capture_logs() as events:
        resolve_task_evidence(impact, deps)

    [event] = [e for e in events if e.get("event") == "page_evidence_empty"]
    assert sorted(event["deps"]) == ["context", "research"]
    assert event["items"] == 1
    assert event["unresolved_pages"] == [unresolved]
    assert event["resolved_pages"] == 1


def test_resolver_leaves_unmatched_pages_empty_for_escalation() -> None:
    """The escalation path must stay reachable: no source matched."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=_tasks_only("docs/a.md"),
    )
    [task] = resolve_task_evidence(impact, {})
    assert task.evidence == []


def test_resolver_tolerates_malformed_dep_payloads() -> None:
    """parse_node_input hands us whatever the section contained."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md"],
        tasks=_tasks_only("docs/a.md"),
    )
    deps = {"context": "## Research Summary: PR #67", "research": None}
    [task] = resolve_task_evidence(impact, deps)
    assert task.evidence == []


def test_resolver_keeps_affected_documents_expansion_path() -> None:
    """No LLM tasks: the resolver still expands and fills affected_documents."""
    impact = ImpactAnalysis(
        action="update",
        affected_documents=["docs/a.md", "docs/b.md"],
        rationale="behavior changed",
    )
    deps = {"context": {"items": [{"id": "docs/b.md", "topic": "widgets"}]}}
    tasks = resolve_task_evidence(impact, deps)
    assert [t.path for t in tasks] == ["docs/a.md", "docs/b.md"]
    assert tasks[0].evidence == []
    assert [item.id for item in tasks[1].evidence] == ["docs/b.md"]
