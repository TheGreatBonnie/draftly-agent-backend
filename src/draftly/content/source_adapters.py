"""Normalize supported source payloads into ``ContentRequest``."""

from __future__ import annotations

from typing import Any

from draftly.content.models import ContentChannel, ContentRequest
from draftly.feedback.models import ContentOpportunity


def _channels(value: Any) -> list[ContentChannel]:
    return [ContentChannel(str(item)) for item in (value or ["blog", "linkedin", "x"])]


def from_github_event(org_id: str, event: dict[str, Any]) -> ContentRequest:
    repository_id = str(event.get("repository_id") or event.get("repository", {}).get("id") or "")
    event_type = str(event.get("source_event_type") or event.get("event_type") or "release")
    return ContentRequest(
        org_id=org_id,
        repository_id=repository_id,
        source_event_id=str(event.get("source_event_id") or event.get("id") or ""),
        source_event_type=event_type,
        source_title=str(event.get("source_title") or event.get("title") or "GitHub update"),
        source_summary=str(event.get("source_summary") or event.get("body") or ""),
        source_feedback_ids=list(event.get("source_feedback_ids") or []),
        source_gap_id=event.get("source_gap_id"),
        source_evidence=list(event.get("source_evidence") or []),
        requested_channels=_channels(event.get("requested_channels")),
        audience=str(event.get("audience") or "developers and users"),
        tone=str(event.get("tone") or "clear, practical, trustworthy"),
    )


def from_documentation_source(org_id: str, source: dict[str, Any]) -> ContentRequest:
    return ContentRequest(
        org_id=org_id,
        repository_id=str(source.get("repository_id") or source.get("repository") or ""),
        source_event_id=str(
            source.get("source_event_id") or source.get("id") or source.get("path") or ""
        ),
        source_event_type="documentation",
        source_title=str(source.get("title") or source.get("path") or "Documentation update"),
        source_summary=str(source.get("summary") or source.get("content") or ""),
        source_evidence=list(source.get("source_evidence") or [{"source_id": source.get("id")}]),
        requested_channels=_channels(source.get("requested_channels")),
        audience=str(source.get("audience") or "developers and users"),
        tone=str(source.get("tone") or "clear, practical, trustworthy"),
    )


def from_manual_brief(org_id: str, brief: dict[str, Any]) -> ContentRequest:
    return ContentRequest(
        org_id=org_id,
        repository_id=str(brief.get("repository_id") or "manual"),
        source_event_id=str(brief.get("source_event_id") or brief.get("id") or "manual-brief"),
        source_event_type="manual_brief",
        source_title=str(brief.get("title") or "Manual content brief"),
        source_summary=str(brief.get("summary") or brief.get("brief") or ""),
        source_evidence=list(brief.get("source_evidence") or []),
        requested_channels=_channels(brief.get("requested_channels")),
        audience=str(brief.get("audience") or "developers and users"),
        tone=str(brief.get("tone") or "clear, practical, trustworthy"),
    )


def from_content_opportunity(org_id: str, opportunity: ContentOpportunity) -> ContentRequest:
    return ContentRequest(
        org_id=org_id,
        repository_id=str(opportunity.model_dump().get("repository_id") or "feedback"),
        source_event_id=opportunity.gap_id,
        source_event_type="feedback_gap",
        source_title=opportunity.topic,
        source_summary=opportunity.reason,
        source_feedback_ids=opportunity.source_feedback_ids,
        source_gap_id=opportunity.gap_id,
        source_evidence=opportunity.evidence or [{"source_id": opportunity.gap_id}],
        requested_channels=_channels(opportunity.recommended_channels),
        audience="users with the recurring question",
        tone="clear and helpful",
    )
