"""Structured output contracts for content-production agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContentBriefOutput(BaseModel):
    title: str
    message: str
    audience: str
    tone: str
    key_claims: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    gap_id: str | None = None


class ContentDraftOutput(BaseModel):
    title: str
    summary: str
    body: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    gap_id: str | None = None


class ContentSocialOutput(BaseModel):
    channel: str
    title: str
    body: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    gap_id: str | None = None
