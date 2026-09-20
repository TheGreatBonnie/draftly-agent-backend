"""derive_page_type / map_question_type are deterministic and total.

Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 6).
"""

from __future__ import annotations

import pytest

from draftly.documentation.page_type import (
    PAGE_TYPES,
    derive_page_type,
    map_question_type,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("https://docs.example.com/tutorials/quickstart", "tutorial"),
        ("https://docs.example.com/getting-started/install", "tutorial"),
        ("https://docs.example.com/guides/deploy", "how-to"),
        ("https://docs.example.com/how-to/configure", "how-to"),
        ("https://docs.example.com/api/reference/auth", "reference"),
        ("https://docs.example.com/sdk/syntax", "reference"),
        ("https://docs.example.com/concepts/architecture", "explanation"),
        ("https://docs.example.com/learn/overview", "explanation"),
        ("https://docs.example.com/", "index"),
        ("https://docs.example.com/README", "index"),
        ("https://docs.example.com/changelog", "index"),
    ],
)
def test_derive_page_type_tokens(path: str, expected: str) -> None:
    assert derive_page_type(path) == expected


def test_derive_page_type_always_returns_known_type() -> None:
    assert derive_page_type("https://docs.example.com/some/random/page") in PAGE_TYPES


def test_map_question_type_priorities() -> None:
    assert map_question_type("signatures") == ("reference",)
    assert map_question_type("parameters") == ("reference",)
    assert map_question_type("procedures") == ("how-to", "tutorial")
    assert map_question_type("concepts") == ("explanation",)
    assert set(map_question_type("general")) == set(PAGE_TYPES)
    assert set(map_question_type("anything-unknown")) == set(PAGE_TYPES)
