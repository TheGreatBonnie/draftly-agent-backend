"""Evidence fields must have a length the model can actually finish.

``EvidenceItem`` is four free-form strings with ``extra="allow"`` and nothing
constraining their size. The context agent must emit the whole
``EvidenceBundle`` inside one response, because strands' ``structured_output``
path is non-streaming. So a model that pastes file contents into ``excerpt``
fills the 16,384-token budget with evidence and never reaches the closing
brace of the tool call -- which is the ``max_tokens`` failure in run ce8ea540,
and the truncated tool JSON seen in the earlier 679-tool-call burst.

A cap in the schema is visible to the model in the tool definition, so it
budgets against it instead of discovering it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.agents.schemas import EvidenceBundle, EvidenceItem

# Comfortably above a real citation, far below a file's worth of text.
EXCERPT_MAX = 2_000


def test_a_normal_excerpt_validates() -> None:
    item = EvidenceItem(id="a.py:10", url="blob:1", topic="auth flow", excerpt="def login(): ...")

    assert item.excerpt.startswith("def login")


def test_an_oversized_excerpt_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(excerpt="x" * (EXCERPT_MAX + 1))


def test_an_excerpt_at_the_cap_is_accepted() -> None:
    item = EvidenceItem(excerpt="x" * EXCERPT_MAX)

    assert len(item.excerpt) == EXCERPT_MAX


def test_an_oversized_topic_is_rejected() -> None:
    """A one-line coverage topic cannot legitimately run to 100k characters."""
    with pytest.raises(ValidationError):
        EvidenceItem(topic="t" * 10_000)


def test_an_oversized_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceItem(url="u" * 10_000)


def test_the_cap_is_visible_in_the_tool_schema() -> None:
    """The model budgets against the schema, so the cap must be in the JSON."""
    schema = EvidenceItem.model_json_schema()

    assert schema["properties"]["excerpt"]["maxLength"] == EXCERPT_MAX
    assert "maxLength" in schema["properties"]["topic"]
    assert "maxLength" in schema["properties"]["url"]


def test_a_bundle_of_normal_items_still_validates() -> None:
    bundle = EvidenceBundle(
        items=[
            EvidenceItem(id="a.py", topic="auth", excerpt="short"),
            EvidenceItem(id="b.py", topic="token", excerpt="shorter"),
        ],
        summary="two files",
    )

    assert len(bundle.items) == 2


def test_extra_keys_still_survive() -> None:
    """Legacy freeform payloads must keep working; only known fields are capped."""
    item = EvidenceItem(excerpt="short", custom_field="anything")

    assert item.model_dump().get("custom_field") == "anything"
