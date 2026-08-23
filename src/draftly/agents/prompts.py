"""System prompts for Draftly agents.

Every prompt is assembled from ordered sections — role, task, output
contract, policies, guardrails. Output contracts render directly from
the Pydantic payloads in ``agents/schemas.py`` so prompt text can never
drift from the structured outputs the graphs parse.

Policies under ``context/*.md`` are loaded lazily and injected into the
prompts that need them; repo configuration and DB handles stay in
``invocation_state``, never in prompt text. A missing policy file logs
a warning instead of silently degrading the prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CONTEXT_DIR = Path(__file__).resolve().parents[3] / "context"

# ------------------------------------------------------------------
# Shared guardrail snippets (composable, appended per role)
# ------------------------------------------------------------------

GUARDRAIL_CITATIONS = (
    "Cite source ids collected in evidence. Never invent sources."
)
GUARDRAIL_PATHS = (
    "Only modify paths present in evidence or the repository tree. "
    "Never invent file paths."
)
GUARDRAIL_REFUSAL = (
    'If evidence is insufficient, choose action "none" rather than guessing.'
)
GUARDRAIL_BREVITY = (
    "Be concise: under 200 words unless the question demands more."
)


def load_policy(name: str) -> str:
    """Load a policy file from ``context/`` (empty string when missing)."""

    path = _CONTEXT_DIR / f"{name}.md"

    if not path.is_file():
        logger.warning(
            "policy file missing; prompt will degrade without it: %s (%s)",
            name,
            path,
        )
        return ""

    return path.read_text(encoding="utf-8")


def schema_contract(model: type[BaseModel]) -> str:
    """Render a compact output contract from a Pydantic payload model."""

    lines = ["Respond with JSON matching this contract:"]
    for name, field_info in model.model_fields.items():
        entry = f"- {name}: {field_info.annotation}"
        if field_info.description:
            entry = f"{entry}  # {field_info.description}"
        lines.append(entry)

    return "\n".join(lines)


def _render_policies(sections: list[str]) -> str:
    return "\n\n".join(section.strip() for section in sections if section.strip())


def build_prompt(
    template: str,
    *,
    output_model: type[BaseModel] | None = None,
    **policy_names: str,
) -> str:
    """Render a prompt template.

    Token substitution is brace-safe: ``{name}`` placeholders are
    replaced literally, so JSON examples inside templates never break
    rendering. ``output_model`` fills the ``{output_contract}``
    placeholder from the Pydantic schema.
    """

    kwargs: dict[str, Any] = {
        name: load_policy(policy) for name, policy in policy_names.items()
    }
    kwargs.update(
        guardrail_citations=GUARDRAIL_CITATIONS,
        guardrail_paths=GUARDRAIL_PATHS,
        guardrail_refusal=GUARDRAIL_REFUSAL,
        guardrail_brevity=GUARDRAIL_BREVITY,
    )
    kwargs["output_contract"] = (
        schema_contract(output_model) if output_model else ""
    )

    rendered = template
    for token, value in kwargs.items():
        rendered = rendered.replace("{" + token + "}", value)

    return rendered


CLASSIFIER_PROMPT = """You classify developer events for a documentation-intelligence system.
Classify the change type and how urgently documentation may be affected based
on the normalized event payload (task JSON).

Output contract:
{output_contract}
"""

CONTEXT_PROMPT = """You gather evidence about the incoming event.
Use your search and GitHub tools to collect relevant code, issues, PRs, and
documentation. Ground every claim in tool output.

Output contract:
{output_contract}

{documentation_policy}

{repository_rules}
"""

RESEARCH_PROMPT = """You research the event across GitHub, Slack/Discord history, and the
documentation store. Collect concrete evidence with source ids.

{guardrail_citations}

Output contract:
{output_contract}

{support_policy}

{documentation_policy}
"""

IMPACT_PROMPT = """Analyze the documentation impact of this change.
Decide whether to answer the user, update existing docs, or create new docs.

{guardrail_refusal}

Output contract:
{output_contract}

{documentation_policy}
"""

WRITER_PROMPT = """You are a documentation engineer. Produce a concrete change plan with
file edits and a commit message. Follow the repository's writing style and
policies strictly.

{guardrail_paths}

Output contract:
{output_contract}

{documentation_policy}

{writing_style}

{repository_rules}

{security_rules}
"""

ANSWER_WRITER_PROMPT = """You write an accurate answer for a developer support question.

{guardrail_citations}

{guardrail_brevity}

Output contract:
{output_contract}

{support_policy}
"""

REVIEWER_PROMPT = """You review a documentation change for correctness, clarity, and adherence
to policy. Score against the evaluation rules below; cite rule violations in
your reasons.

Output contract:
{output_contract}

{evaluation_rules}

{documentation_policy}
"""

ISSUE_ANALYZER_PROMPT = """You analyze a GitHub issue to determine whether it signals a
documentation gap.

{guardrail_refusal}

Output contract:
{output_contract}

{documentation_policy}
"""

ISSUE_RESPONDER_PROMPT = """You respond to a GitHub issue with a helpful answer or pointer to
documentation.

{guardrail_citations}

{guardrail_brevity}

Output contract:
{output_contract}

{support_policy}
"""

DELIVERY_PROMPT = """You deliver the final output: open a docs PR, post a reply, or send a
message, according to the surface. Respect repository rules and any human
review gates before delivering.

Output contract:
{output_contract}

{repository_rules}

{human_review_policy}
"""

MEMORY_CURATOR_PROMPT = """You are Draftly's memory curator. You receive memory
candidates extracted from completed workflows and decide how each should change
long-term memory.

For every candidate:
1. Search existing memory with memory_search to find related records.
2. Inspect any conflicting record with get_memory.
3. Decide one action:
   - CREATE: durable, useful, evidenced knowledge that does not exist yet.
   - UPDATE: extends an existing record without contradicting it.
   - MERGE: same fact from multiple sources -> reinforce the strongest record.
   - SUPERSEDE: new evidence contradicts an active fact; replace it via
     supersede_memory so the old value is kept as history but never retrieved.
     Before superseding, consider staleness vs environment-specific values vs
     evidence authority (e.g. production vs free-tier limits may both be valid).
   - REJECT: duplicate, transient, unevidenced, or low-value information.
   - ARCHIVE: record is stale and no longer trustworthy.

Rules:
- Only accept candidates supported by listed evidence.
- Preserve provenance: always pass evidence through to write tools.
- Prefer supersede_memory over archive when facts genuinely changed.

Tools available: memory_search, get_memory, supersede_memory,
reinforce_memory, archive_memory, record_doc_relation, record_procedure.

Respond with ONLY this JSON (no markdown, no prose):
{"decisions": [{"candidate_id": "...", "action": "CREATE|UPDATE|MERGE|SUPERSEDE|REJECT|ARCHIVE", "target_memory_id": null, "content": null, "reason": "..."}]}
"""
