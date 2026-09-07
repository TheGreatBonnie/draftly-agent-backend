"""Typed handoff from support-graph documentation gaps to downstream delivery.

Documentation-gap outcomes (``update``/``create``) never post a Slack/Discord
reply; instead they emit a ``DocumentationGapRequest`` that preserves the
originating organization, repository, and source event identity so the
documentation/GitHub delivery path can open a reviewed PR.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentationGapRequest(BaseModel):
    """Typed handoff for a support outcome that requires documentation work."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    repository: str = ""
    base_branch: str = "main"
    source_event_ids: list[str] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    base_sha: str | None = None
    change_plan: dict[str, Any] | None = None
