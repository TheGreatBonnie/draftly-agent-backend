"""Steering intervention API request/response contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class InterventionAction(StrEnum):
    APPROVE = "approve"
    DENY = "deny"
    GUIDE = "guide"


class InterventionResponseRequest(BaseModel):
    """A human decision on one durable steering intervention."""

    action: InterventionAction
    message: str | None = Field(default=None, max_length=1_000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class PendingInterventionSummary(BaseModel):
    """Safe context needed to render a pending steering decision."""

    interrupt_id: str
    status: str
    phase: str | None = None
    role: str | None = None
    rule: str | None = None
    reason: str | None = None
    agent_id: str | None = None
    node_id: str | None = None
    tool_name: str | None = None
    created_at: object | None = None
    expires_at: object | None = None


class InterventionResponse(BaseModel):
    """Safe, bounded echo of the resolved intervention.

    ``reason`` and tool arguments are never surfaced; only the persisted
    outcome, the caller's own (scrubbed) message, and the run status are
    returned.
    """

    intervention_id: str | None = None
    interrupt_id: str = ""
    status: str = ""
    resolver: str | None = None
    response_message: str | None = None
    run_status: str | None = None
