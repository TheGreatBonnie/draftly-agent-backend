"""Invariant tests for the structured system-prompt builder."""

from __future__ import annotations

import logging

import pytest

from draftly.agents import prompts
from draftly.agents.prompts import (
    ANSWER_WRITER_PROMPT,
    CLASSIFIER_PROMPT,
    CONTEXT_PROMPT,
    DELIVERY_PROMPT,
    IMPACT_PROMPT,
    ISSUE_ANALYZER_PROMPT,
    ISSUE_RESPONDER_PROMPT,
    RESEARCH_PROMPT,
    REVIEWER_PROMPT,
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
