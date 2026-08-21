"""Security audit logging (plan §8.8)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

audit_logger = logging.getLogger("draftly.security.audit")


@dataclass
class AuditEvent:
    """One security-relevant action."""

    action: str
    actor: str | None = None
    org_id: str | None = None
    resource: str | None = None
    outcome: str = "allowed"
    detail: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "actor": self.actor,
            "org_id": self.org_id,
            "resource": self.resource,
            "outcome": self.outcome,
            "detail": self.detail,
            "occurred_at": self.occurred_at.isoformat(),
        }


class SecurityAuditLogger:
    """Structured audit trail via the ``draftly.security.audit`` logger."""

    def __init__(self, logger_: logging.Logger | None = None) -> None:
        self.logger = logger_ or audit_logger

    def record(self, event: AuditEvent) -> None:
        self.logger.info(json.dumps(event.to_dict(), default=str))

    def denied(
        self,
        *,
        action: str,
        actor: str | None = None,
        org_id: str | None = None,
        resource: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            action=action,
            actor=actor,
            org_id=org_id,
            resource=resource,
            outcome="denied",
            detail=detail or {},
        )
        self.record(event)
        return event

    def allowed(
        self,
        *,
        action: str,
        actor: str | None = None,
        org_id: str | None = None,
        resource: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            action=action,
            actor=actor,
            org_id=org_id,
            resource=resource,
            outcome="allowed",
            detail=detail or {},
        )
        self.record(event)
        return event
