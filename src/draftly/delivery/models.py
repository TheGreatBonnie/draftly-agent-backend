"""Delivery domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeliveryPlan(BaseModel):
    """A planned documentation delivery."""

    model_config = ConfigDict(extra="allow")

    id: str
    repository_id: str | None = None
    repository_path: str | None = None
    summary: str = ""
    changes: list[Any] = []
    created_at: datetime | None = None
    status: str = "planned"
    org_id: str | None = None
    run_id: str | None = None


class CommitResult(BaseModel):
    """Result of creating a commit."""

    model_config = ConfigDict(extra="allow")

    repository_id: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    message: str = ""
    files: list[Any] = []
    created_at: datetime | None = None
    org_id: str | None = None
    run_id: str | None = None


class PullRequestResult(BaseModel):
    """Result of creating a pull request."""

    model_config = ConfigDict(extra="allow")

    repository_id: str | None = None
    owner: str | None = None
    repository: str | None = None
    number: int | None = None
    url: str | None = None
    title: str = ""
    branch: str | None = None
    base_branch: str | None = None
    created_at: datetime | None = None
    org_id: str | None = None
    run_id: str | None = None


class SupportDeliveryReceipt(BaseModel):
    """Receipt for a delivered Slack/Discord support reply."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    org_id: str | None = None
    platform: str = ""
    channel_id: str | None = None
    thread_id: str | None = None
    source_message_id: str | None = None
    provider_message_id: str | None = None
    status: str = "delivered"  # delivered | failed | pending
    error: str | None = None
    delivered_at: datetime | None = None
