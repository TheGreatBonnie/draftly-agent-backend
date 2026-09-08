"""Content-strategist prompt directives.

The strategist must be explicitly required to run a search before finalizing
the brief; otherwise the `node:content_brief` and `expected_tools` grounding
asserts depend on LLM luck (they passed only on runs where the model happened
to call a search tool).
"""

from draftly.agents.content.prompts import (
    BLOG_WRITER_PROMPT,
    CONTENT_GROUNDING_JUDGE_PROMPT,
    CONTENT_STRATEGIST_PROMPT,
)

import re

_FLAT = lambda p: re.sub(r"\s+", " ", p).lower()


def test_judge_blocks_only_concrete_unestablished_facts() -> None:
    prompt = _FLAT(CONTENT_GROUNDING_JUDGE_PROMPT)
    # Blocking must be reserved for concrete facts the evidence does not
    # establish — never for thematic phrasing or editorial synthesis.
    assert "concrete fact" in prompt
    assert "does not establish" in prompt
    assert "wording, emphasis, or structure" in prompt


def test_judge_must_verify_evidence_before_blocking() -> None:
    prompt = _FLAT(CONTENT_GROUNDING_JUDGE_PROMPT)
    # The judge must re-check the evidence before blocking and must never
    # claim the evidence is missing something the evidence actually contains
    # (a prior run blocked on "evidence does not mention OAuth" while the
    # evidence did mention OAuth).
    assert "re-read" in prompt
    assert "confirm" in prompt
    assert "actually contains" in prompt


def test_blog_writer_avoids_qualitative_judgments() -> None:
    prompt = _FLAT(BLOG_WRITER_PROMPT)
    assert "qualitative" in prompt
    assert "the evidence does not support" in prompt


def test_strategist_must_run_a_search_before_finalizing() -> None:
    prompt = _FLAT(CONTENT_STRATEGIST_PROMPT)
    assert ("search" in prompt) and (
        "keyword_search" in prompt or "semantic_search" in prompt or "code_search" in prompt
    )
    # The directive must not be conditional ("if you want") — it must be
    # mandatory ("run at least one ... before finalizing").
    assert "run at least one" in prompt
    assert "before finalizing" in prompt
    assert "if you" not in prompt
