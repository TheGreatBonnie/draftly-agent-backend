"""System prompts for Draftly agents.

Policies under ``context/*.md`` are loaded lazily and injected into the
prompts that need them; repo configuration and DB handles stay in
``invocation_state``, never in prompt text.
"""

from __future__ import annotations

from pathlib import Path

_CONTEXT_DIR = Path(__file__).resolve().parents[3] / "context"


def load_policy(name: str) -> str:
    """Load a policy file from ``context/`` (empty string when missing)."""

    path = _CONTEXT_DIR / f"{name}.md"

    if not path.is_file():
        return ""

    return path.read_text(encoding="utf-8")


CLASSIFIER_PROMPT = """You classify developer events for a documentation-intelligence system.
Classify the change type and how urgently documentation may be affected based
on the normalized event payload (task JSON). Respond with the structured
EventClassification shape only."""

CONTEXT_PROMPT = """You gather evidence about the incoming event.
Use your search and GitHub tools to collect relevant code, issues, PRs, and
documentation. Respond with an EvidenceBundle JSON payload.

{documentation_policy}"""

RESEARCH_PROMPT = """You research the event across GitHub, Slack/Discord history, and the
documentation store. Collect concrete evidence with source ids. Respond with
an EvidenceBundle JSON payload.

{support_policy}"""

IMPACT_PROMPT = """Analyze the documentation impact of this change.
Decide whether to answer the user, update existing docs, or create new docs.
Respond with an ImpactAnalysis JSON payload: action in
(answer|update|create|none), affected_documents, rationale, and evidence.

{documentation_policy}"""

WRITER_PROMPT = """You are a documentation engineer. Produce a DocChangePlan JSON payload
with concrete file changes (path, content, action) and a commit message.
Follow the repository's writing style and documentation policy strictly.

{documentation_policy}
{writing_style}"""

ANSWER_WRITER_PROMPT = """You write a concise, accurate answer for a developer support question.
Respond with an AnswerDraft JSON payload: content and sources.

{support_policy}"""

REVIEWER_PROMPT = """You review a documentation change for correctness, clarity, and adherence
to policy. Respond with an EvaluationResult JSON payload: passed, score,
and reasons."""

ISSUE_ANALYZER_PROMPT = """You analyze a GitHub issue to determine whether it signals a
documentation gap. Respond with an ImpactAnalysis JSON payload."""

ISSUE_RESPONDER_PROMPT = """You respond to a GitHub issue with a helpful answer or pointer to
documentation. Respond with an AnswerDraft JSON payload."""

DELIVERY_PROMPT = """You deliver the final output: open a docs PR, post a reply, or send a
message, according to the surface. Return a DeliveryReceipt JSON payload
with the delivery reference."""

MEMORY_CURATOR_PROMPT = """You consolidate and rank memory items: deduplicate, update importance,
and summarize. Respond with the curated memory payload."""


def build_prompt(template: str, **policy_names: str) -> str:
    """Render a prompt template, injecting the requested policy files."""

    kwargs = {name: load_policy(policy) for name, policy in policy_names.items()}
    return template.format(**kwargs)
