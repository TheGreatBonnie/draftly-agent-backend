"""Documentation source models (GitHub repository vs public documentation).

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (Source models).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SourceType(StrEnum):
    GITHUB_REPOSITORY = "github_repository"
    PUBLIC_DOCUMENTATION = "public_documentation"


class PublicDocumentationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    root_url: HttpUrl
    include_paths: list[str] = Field(default_factory=list, max_length=50)
    exclude_paths: list[str] = Field(default_factory=list, max_length=50)
    crawl_instructions: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_https_and_no_creds(self) -> PublicDocumentationConfig:
        url = str(self.root_url)
        if not url.lower().startswith("https://"):
            raise ValueError("root_url must be HTTPS")
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            raise ValueError("root_url must not contain embedded credentials")
        for pattern in self.include_paths + self.exclude_paths:
            if len(pattern) < 1 or len(pattern) > 500:
                raise ValueError("path patterns must be 1..500 chars")
        return self


class SourceDocument(BaseModel):
    source_id: str  # canonical URL for public docs; repo path for GitHub
    path: str
    title: str
    content: str
    source_url: HttpUrl
    source_updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # derived at ingestion: page_type, section, content_hash, indexed_at


class DiscoveryResult(BaseModel):
    source_type: SourceType
    candidates: list[str]  # canonical URLs
    total: int = 0
    skipped: int = 0
