"""Source models: SourceType, PublicDocumentationConfig, SourceDocument.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (Source models).
Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 5).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from draftly.documentation.source_models import (
    DiscoveryResult,
    PublicDocumentationConfig,
    SourceDocument,
    SourceType,
)


def test_source_type_values() -> None:
    assert SourceType.GITHUB_REPOSITORY.value == "github_repository"
    assert SourceType.PUBLIC_DOCUMENTATION.value == "public_documentation"


def test_public_config_valid_constructs() -> None:
    config = PublicDocumentationConfig(
        root_url="https://docs.example.com/",
        include_paths=["/docs/.*"],
        exclude_paths=["/docs/internal/.*"],
    )
    assert str(config.root_url).startswith("https://docs.example.com")
    assert config.crawl_instructions is None


def test_public_config_rejects_http_root() -> None:
    with pytest.raises(ValidationError):
        PublicDocumentationConfig(root_url="http://docs.example.com/")


def test_public_config_rejects_credentials_in_url() -> None:
    with pytest.raises(ValidationError):
        PublicDocumentationConfig(root_url="https://user:pass@docs.example.com/")


def test_public_config_rejects_oversized_path_list() -> None:
    with pytest.raises(ValidationError):
        PublicDocumentationConfig(
            root_url="https://docs.example.com/",
            include_paths=[f"/p{i}" for i in range(51)],
        )


def test_public_config_rejects_path_pattern_too_long() -> None:
    with pytest.raises(ValidationError):
        PublicDocumentationConfig(
            root_url="https://docs.example.com/",
            include_paths=["/x" * 251],
        )


def test_public_config_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PublicDocumentationConfig(
            root_url="https://docs.example.com/",
            unknown_field="nope",
        )


def test_source_document_defaults() -> None:
    doc = SourceDocument(
        source_id="https://docs.example.com/a",
        path="a",
        title="A",
        content="# A",
        source_url="https://docs.example.com/a",
    )
    assert doc.source_updated_at is None
    assert doc.metadata == {}


def test_discovery_result_shape() -> None:
    result = DiscoveryResult(
        source_type=SourceType.PUBLIC_DOCUMENTATION,
        candidates=["https://docs.example.com/a"],
        total=1,
        skipped=2,
    )
    assert result.total == 1
    assert result.skipped == 2
