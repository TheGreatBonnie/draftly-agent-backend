"""Invariant tests for the structured system-prompt builder."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from draftly.agents import prompts
from draftly.agents.prompts import (
    ANSWER_WRITER_PROMPT,
    CLASSIFIER_PROMPT,
    CONTEXT_PROMPT,
    DELIVERY_PROMPT,
    DOC_CONTEXT_PROMPT,
    IMPACT_PROMPT,
    ISSUE_ANALYZER_PROMPT,
    ISSUE_LOCAL_RESEARCHER_PROMPT,
    ISSUE_RESPONDER_PROMPT,
    RESEARCH_PROMPT,
    REVIEWER_PROMPT,
    SUPPORT_LOCAL_RESEARCHER_PROMPT,
    WRITER_PROMPT,
    build_prompt,
)
from draftly.agents.schemas import (
    AnswerDraft,
    DeliveryReceipt,
    DocChangePlan,
    EvaluationResult,
    EventClassification,
    EvidenceBundle,
    ImpactAnalysis,
)


class TestSchemaContract:
    def test_lists_field_names(self) -> None:
        contract = prompts.schema_contract(ImpactAnalysis)

        assert "action" in contract
        assert "affected_documents" in contract

    def test_lists_enum_values_from_descriptions(self) -> None:
        contract = prompts.schema_contract(EventClassification)

        assert "breaking_change" in contract
        assert '"pull_request"' in contract

    def test_forbids_unescaped_newlines_in_string_values(self) -> None:
        contract = prompts.schema_contract(ImpactAnalysis)

        assert "\\n" in contract  # instructs literal backslash-n for newlines
        assert "actual line break" in contract
        assert "REJECTED" in contract


#: prompt -> (payload model, substrings that MUST appear when rendered)
RENDER_MATRIX = (
    (CLASSIFIER_PROMPT, EventClassification, ("change_type", "urgency")),
    (CONTEXT_PROMPT, EvidenceBundle, ("items", "summary")),
    (RESEARCH_PROMPT, EvidenceBundle, ("items", "summary")),
    (IMPACT_PROMPT, ImpactAnalysis, ("affected_documents", "rationale")),
    (WRITER_PROMPT, DocChangePlan, ("commit_message", "files")),
    (ANSWER_WRITER_PROMPT, AnswerDraft, ("sources",)),
    (REVIEWER_PROMPT, EvaluationResult, ("passed", "score")),
    (ISSUE_ANALYZER_PROMPT, ImpactAnalysis, ("action",)),
    (ISSUE_RESPONDER_PROMPT, AnswerDraft, ("sources",)),
    (DELIVERY_PROMPT, DeliveryReceipt, ("reference", "status")),
)


class TestRenderedPromptsCarryOutputContract:
    @pytest.mark.parametrize(("template", "model", "fields"), RENDER_MATRIX)
    def test_payload_fields_present(
        self,
        template: str,
        model: type,
        fields: tuple[str, ...],
    ) -> None:
        rendered = build_prompt(template, output_model=model)

        for field_name in fields:
            assert field_name in rendered


class TestPolicyInjectionMatrix:
    def test_writer_gets_style_repo_and_security_policies(self) -> None:
        rendered = build_prompt(
            WRITER_PROMPT,
            output_model=DocChangePlan,
            documentation_policy="documentation_policy",
            writing_style="writing_style",
            repository_rules="repository_rules",
            security_rules="security_rules",
        )

        assert "# Documentation Policy" in rendered
        assert "# Writing Style" in rendered
        assert "# Repository Rules" in rendered
        assert "# Security Rules" in rendered

    def test_reviewer_anchored_to_evaluation_rules(self) -> None:
        rendered = build_prompt(
            REVIEWER_PROMPT,
            output_model=EvaluationResult,
            evaluation_rules="evaluation_rules",
            documentation_policy="documentation_policy",
        )

        assert "# Evaluation Rules" in rendered
        assert "# Documentation Policy" in rendered

    def test_research_includes_both_policies(self) -> None:
        rendered = build_prompt(
            RESEARCH_PROMPT,
            output_model=EvidenceBundle,
            support_policy="support_policy",
            documentation_policy="documentation_policy",
        )

        assert "# Support Policy" in rendered
        assert "# Documentation Policy" in rendered

    def test_delivery_gets_review_policy(self) -> None:
        rendered = build_prompt(
            DELIVERY_PROMPT,
            output_model=DeliveryReceipt,
            repository_rules="repository_rules",
            human_review_policy="human_review_policy",
        )

        assert "# Human Review Policy" in rendered


class TestGuardrailsPresent:
    def test_writer_never_invents_paths(self) -> None:
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)

        assert "Never invent file paths" in rendered

    def test_research_never_invents_sources(self) -> None:
        rendered = build_prompt(
            RESEARCH_PROMPT,
            output_model=EvidenceBundle,
            support_policy="support_policy",
        )

        assert "Never invent sources" in rendered

    def test_impact_refusal_rule(self) -> None:
        rendered = build_prompt(IMPACT_PROMPT, output_model=ImpactAnalysis)

        assert "insufficient" in rendered.lower()

    def test_answer_brevity_rule(self) -> None:
        rendered = build_prompt(ANSWER_WRITER_PROMPT, output_model=AnswerDraft)

        assert "concise" in rendered.lower()

    def test_answer_grounds_symbols_verbatim_in_evidence(self) -> None:
        rendered = build_prompt(ANSWER_WRITER_PROMPT, output_model=AnswerDraft)
        flat = " ".join(rendered.split())

        assert "verbatim" in flat
        assert "never rename" in flat
        assert "hallucination" in flat
        assert "permissions.list_for_user()" in flat  # explicit anti-renaming example
        assert "roles.list_for_user" in flat
        assert "if they appear verbatim" in flat

    def test_writer_prompt_has_revision_feedback_block(self) -> None:
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)

        assert "Revision pass" in rendered
        assert "evaluate reasons" in rendered or "reasons" in rendered

    def test_writer_revision_handles_deterministic_gate_reasons(self) -> None:
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)
        flat = " ".join(rendered.split())

        assert "Grounded in" in flat
        assert "Covers" in flat
        assert "deterministic gate" in flat

    def test_writer_revision_acts_on_missing_topics_feedback(self) -> None:
        """The deterministic gate now names uncovered topics via a
        ``Missing topics: ...`` reason; the writer must be told to author a
        real section for each named topic, or the feedback has no teeth."""
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)
        flat = " ".join(rendered.split())

        assert "Missing topics" in flat
        assert "each named" in flat
        assert "real section" in flat

    def test_revision_prompts_use_human_review_feedback(self) -> None:
        writer = " ".join(build_prompt(WRITER_PROMPT, output_model=DocChangePlan).split())
        context = " ".join(build_prompt(DOC_CONTEXT_PROMPT, output_model=EvidenceBundle).split())

        assert "review_feedback" in writer
        assert "review_feedback" in context
        assert "revision" in writer.lower()

    def test_evaluation_rules_match_deterministic_weights(self) -> None:
        rendered = build_prompt(
            REVIEWER_PROMPT,
            output_model=EvaluationResult,
            evaluation_rules="evaluation_rules",
        )

        assert "# Evaluation Rules" in rendered
        flat = " ".join(prompts.EVALUATION_RULES.split())
        assert "0.4" in flat
        assert "0.3" in flat


class TestImpactPromptGrounding:
    def test_doc_impact_prompt_requires_search_before_verdict(self) -> None:
        """The documentation impact node must run at least one retrieval search
        before choosing an action so it satisfies the required-tools contract
        (node:impact evaluator) instead of reading/deciding straight away."""
        flat = " ".join(IMPACT_PROMPT.split())

        assert "at least one" in flat
        assert "semantic or keyword" in flat
        assert "semantic_search" in flat
        assert "keyword_search" in flat

    def test_issue_analyzer_prompt_requires_search_before_verdict(self) -> None:
        flat = " ".join(ISSUE_ANALYZER_PROMPT.split())

        assert "at least one" in flat
        assert "semantic or keyword" in flat
        assert "semantic_search" in flat
        assert "keyword_search" in flat

    def test_research_prompt_requires_grounding_search(self) -> None:
        """RESEARCH_PROMPT is shared by the support impact node (solution
        researcher) and the issue researcher; it must demand a search before
        reporting so the impact node satisfies the required-tools contract
        instead of skipping tool-grounding when context already gathered."""
        flat = " ".join(RESEARCH_PROMPT.split())

        assert "at least one" in flat
        assert "semantic or keyword" in flat
        assert "semantic_search" in flat
        assert "keyword_search" in flat
        assert "code_search" in flat


class TestIssueLocalResearcherPrompt:
    def test_local_researcher_requires_grounding_search(self) -> None:
        """The issue research node's local researcher must demand at least one
        retrieval/code-search before returning evidence, so the ``research``
        node satisfies the dataset's required-tools contract instead of
        skipping tool-grounding when context already gathered."""
        flat = " ".join(ISSUE_LOCAL_RESEARCHER_PROMPT.split())

        assert "at least one" in flat
        assert "semantic or keyword" in flat
        assert "semantic_search" in flat
        assert "keyword_search" in flat
        assert "code_search" in flat


class TestSupportLocalResearcherPrompt:
    def test_support_local_researcher_requires_grounding_search(self) -> None:
        flat = " ".join(SUPPORT_LOCAL_RESEARCHER_PROMPT.split())

        assert "at least one" in flat
        assert "semantic or keyword" in flat
        assert "semantic_search" in flat
        assert "keyword_search" in flat
        assert "code_search" in flat


class TestLocalRepoNoteConditional:
    def test_default_still_injects_local_repo_note(self) -> None:
        rendered = build_prompt(
            DOC_CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
        )

        assert "LOCAL checkout" in rendered

    def test_empty_note_omits_local_repo_note(self) -> None:
        rendered = build_prompt(
            DOC_CONTEXT_PROMPT,
            output_model=EvidenceBundle,
            documentation_policy="documentation_policy",
            repository_rules="repository_rules",
            local_repo_note="",
        )

        assert "LOCAL checkout" not in rendered


class TestLocalRepoNoteFor:
    def test_no_checkout_renders_empty(self) -> None:
        from draftly.agents.prompts import local_repo_note_for

        assert local_repo_note_for(None) == ""

    def test_concrete_checkout_path_is_substituted(self) -> None:
        from draftly.agents.prompts import local_repo_note_for

        assert "repo_dir=/data/authly" in local_repo_note_for("/data/authly")
        assert "<local checkout path>" not in local_repo_note_for("/data/authly")


class TestBuildPromptHardening:
    def test_literal_braces_survive_rendering(self) -> None:
        rendered = build_prompt(
            'Return JSON like {"ok": true}. Contract:\n{output_contract}',
            output_model=AnswerDraft,
        )

        assert '{"ok": true}' in rendered
        assert "sources" in rendered

    def test_missing_policy_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            rendered = build_prompt(
                CONTEXT_PROMPT,
                documentation_policy="no_such_policy_file",
            )

        assert rendered  # still renders
        assert any("no_such_policy_file" in r.message for r in caplog.records)


class TestInlinePoliciesSelfContained:
    def test_all_known_policies_resolve_inline_without_context_dir(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Prompts must be fully self-contained: policy names resolve to inline
        # constants and never read from context/*.md. Make any accidental file
        # read fail loudly.
        import os

        def deny_read(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("policy content must be inline, not read from disk")

        monkeypatch.setattr(os, "read", deny_read, raising=False)
        monkeypatch.setattr(Path, "read_text", deny_read, raising=False)

        from draftly.agents.prompts import _POLICIES

        assert "documentation_policy" in _POLICIES
        assert "# Documentation Policy" in _POLICIES["documentation_policy"]
        assert "# Writing Style" in _POLICIES["writing_style"]
        assert "# Repository Rules" in _POLICIES["repository_rules"]
        assert "# Security Rules" in _POLICIES["security_rules"]
        assert "# Evaluation Rules" in _POLICIES["evaluation_rules"]
        assert "# Support Policy" in _POLICIES["support_policy"]
        assert "# Human Review Policy" in _POLICIES["human_review_policy"]

    def test_load_policy_returns_inline_content(self) -> None:
        from draftly.agents.prompts import load_policy

        assert "grounded" in load_policy("documentation_policy").lower()
        assert load_policy("no_such_policy") == ""


class TestLoadSkills:
    def test_load_skills_returns_strands_skill_instances(self) -> None:
        from draftly.agents.prompts import load_skills

        skills = load_skills("documentation-update", "documentation-generation")

        assert len(skills) == 2
        assert {s.name for s in skills} == {
            "documentation-update",
            "documentation-generation",
        }
        for skill in skills:
            assert skill.path is not None  # keeps path so references/ assets resolve

    def test_load_skills_skips_missing_dir(self) -> None:
        from draftly.agents.prompts import load_skills

        skills = load_skills("does-not-exist")
        assert skills == []


class TestWriterPromptDiataxisDirective:
    def test_writer_demands_concrete_diff_coverage(self) -> None:
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)

        assert "Author exactly what the diff adds" in rendered
        assert "Diátaxis structure" in rendered
        assert "How-to guide" in rendered
        assert "Reference" in rendered
        assert "Completeness check" in rendered

    def test_writer_scope_authoritative_and_full_coverage(self) -> None:
        rendered = build_prompt(WRITER_PROMPT, output_model=DocChangePlan)
        flat = " ".join(rendered.split())

        assert "## Scope: author what the task requires" in rendered
        assert "TASK INPUT is authoritative" in flat
        assert "Do NOT conclude the docs already cover the change" in flat
        assert "those areas explicitly" in flat
        assert "authorization URL" in flat
        assert "access token" in flat
        assert "callback" in flat
        assert "provider" in flat
        assert "completeness" in flat.lower()


class TestImpactPromptRoutingDirective:
    def test_impact_routes_to_authoring_when_gap_stated(self) -> None:
        rendered = build_prompt(IMPACT_PROMPT, output_model=ImpactAnalysis)

        assert "task input is authoritative" in rendered
        assert "does not yet describe" in rendered
        assert "Do NOT conclude" in rendered
        assert "Prefer **update**" in rendered
        assert "Prefer **create**" in rendered
