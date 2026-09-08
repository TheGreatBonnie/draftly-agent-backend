"""Evidence item contract: typed items that survive Pydantic validation."""

from __future__ import annotations

from draftly.agents.schemas import EvidenceBundle, EvidenceItem


def test_evidence_bundle_coerces_dict_items_to_evidence_item_models() -> None:
    bundle = EvidenceBundle(
        items=[{"id": "src/oauth.py", "topic": "OAuth", "excerpt": "..."}]
    )

    item = bundle.items[0]
    assert isinstance(item, EvidenceItem)
    assert item.id == "src/oauth.py"
    assert item.topic == "OAuth"


def test_evidence_item_defaults_are_empty_strings() -> None:
    item = EvidenceItem(content="snippet without id")

    assert item.id == ""
    assert item.url == ""
    assert item.topic == ""
    assert item.excerpt == ""


def test_evidence_item_preserves_unknown_fields() -> None:
    bundle = EvidenceBundle(items=[{"content": "legacy freeform dict", "score": 0.9}])

    payload = bundle.model_dump()
    dumped = payload["items"][0]
    assert dumped["content"] == "legacy freeform dict"
    assert dumped["score"] == 0.9
