from __future__ import annotations

import json
import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from draftly.integrations.database.client import DatabaseClient


def _reason_detail(reason: Any) -> dict[str, Any]:
    """Extract structured detail from a review-gate reason.

    The gate builds ``reason = {"run_id", "summary", "evaluation",
    "evidence_count", "document"}`` (review_gate.py) where ``document``
    carries the proposed documentation (writer payload) for the reviewer.
    Only dict reasons carry structured fields; other types degrade to an
    empty detail so the column stays JSONB-null-ish.
    """
    if isinstance(reason, dict):
        return {
            "summary": reason.get("summary"),
            "evaluation": reason.get("evaluation"),
            "evidence_count": reason.get("evidence_count"),
            "run_id": reason.get("run_id"),
            "classification": reason.get("classification"),
            "evidence": reason.get("evidence"),
            "document": reason.get("document"),
            "changelog": reason.get("changelog"),
        }
    return {}


@dataclass
class ReviewRecord:
    id: str
    org_id: str
    thread_id: str
    workflow: str
    tool_name: str
    tool_args: dict[str, Any]
    action_description: str | None
    status: str
    reviewer_id: str | None = None
    decision: str | None = None
    decision_comment: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    notification_sent_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] | None = None


def _normalized_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value) * 100 if value <= 1 else float(value)
    return max(0.0, min(100.0, score))


def review_counts_from_records(records: Iterable[ReviewRecord]) -> dict[str, int]:
    """Calculate review queue counters from organization-scoped records."""
    counts = {
        "pending": 0,
        "urgent": 0,
        "approved": 0,
        "needs_changes": 0,
        "rejected": 0,
    }
    for record in records:
        status = str(record.status or "").strip().lower()
        if status in counts:
            counts[status] += 1
        decision = str(record.decision or "").strip().lower()
        if status != "needs_changes" and decision == "needs_changes":
            counts["needs_changes"] += 1
        detail = record.detail if isinstance(record.detail, dict) else {}
        classification = detail.get("classification")
        classification = classification if isinstance(classification, dict) else {}
        risk = str(
            classification.get("risk")
            or classification.get("risk_level")
            or classification.get("urgency")
            or detail.get("risk")
            or ""
        ).strip().lower()
        evaluation = detail.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        score = _normalized_score(
            evaluation.get("overall_score", evaluation.get("score"))
        )
        if risk in {"high", "critical"} or (score is not None and score < 80):
            counts["urgent"] += 1
    return counts


def _encode_cursor(record: ReviewRecord) -> str:
    value = {
        "created_at": record.created_at.isoformat(),
        "id": record.id,
    }
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        timestamp = datetime.fromisoformat(str(value["created_at"]))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp, str(value["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid review cursor") from exc


class ReviewsRepository:
    """NeonDB repository for review requests and decisions."""

    def __init__(self, database: DatabaseClient | None = None):
        self.database = database or DatabaseClient()

    async def create_review(
        self,
        *,
        org_id: str,
        thread_id: str,
        workflow: str,
        tool_name: str,
        tool_args: dict[str, Any],
        action_description: str | None = None,
        expires_in_hours: int = 24,
    ) -> ReviewRecord:
        review_id = str(uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expires_in_hours)

        query = """
        INSERT INTO reviews (
            id, org_id, thread_id, workflow, tool_name, tool_args,
            action_description, status, created_at, expires_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9)
        """

        await self.database.execute(
            query,
            review_id,
            org_id,
            thread_id,
            workflow,
            tool_name,
            json.dumps(tool_args),
            action_description,
            now,
            expires_at,
        )

        return ReviewRecord(
            id=review_id,
            org_id=org_id,
            thread_id=thread_id,
            workflow=workflow,
            tool_name=tool_name,
            tool_args=tool_args,
            action_description=action_description,
            status="pending",
            created_at=now,
            expires_at=expires_at,
        )

    async def get_review(self, review_id: str) -> ReviewRecord | None:
        query = "SELECT * FROM reviews WHERE id = $1"
        row = await self.database.fetch_one(query, review_id)
        if not row:
            return None
        return self._row_to_record(row)

    async def get_pending_by_run_id(self, run_id: str) -> ReviewRecord | None:
        """Find the pending doc-review interrupt for a workflow run."""
        query = """
        SELECT * FROM reviews
        WHERE thread_id = $1 AND tool_name = 'doc-review'
              AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """
        row = await self.database.fetch_one(query, run_id)
        if not row:
            return None
        return self._row_to_record(row)

    async def record_decision(
        self,
        *,
        review_id: str,
        reviewer_id: str,
        decision: str,
        comment: str | None = None,
    ) -> ReviewRecord:
        now = datetime.now(UTC)
        query = """
        UPDATE reviews
        SET status = $2, reviewer_id = $3, decision = $2,
            decision_comment = $4, decided_at = $5
        WHERE id = $1 AND status = 'pending'
        RETURNING *
        """
        row = await self.database.fetch_one(
            query,
            review_id,
            decision,
            reviewer_id,
            comment,
            now,
        )
        if not row:
            existing = await self.get_review(review_id)
            if existing:
                return existing
            raise ValueError(f"Review {review_id} not found")
        return self._row_to_record(row)

    async def expire_old_reviews(self) -> list[ReviewRecord]:
        now = datetime.now(UTC)
        query = """
        UPDATE reviews
        SET status = 'expired', decision = 'timeout', decided_at = $1
        WHERE status = 'pending' AND expires_at < $1
        RETURNING *
        """
        rows = await self.database.fetch_all(query, now)
        return [self._row_to_record(row) for row in rows]

    async def list_reviews(
        self,
        *,
        status: str | None = None,
        org_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> list[ReviewRecord]:
        clauses = []
        params: list[Any] = []
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if org_id:
            params.append(org_id)
            clauses.append(f"org_id = ${len(params)}")
        if cursor:
            created_at, review_id = _decode_cursor(cursor)
            params.extend([created_at, review_id])
            clauses.append(
                f"(created_at, id) > (${len(params) - 1}, ${len(params)})"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
        SELECT * FROM reviews {where}
        ORDER BY created_at ASC, id ASC
        LIMIT ${len(params) + 1}
        """
        params.append(limit)
        rows = await self.database.fetch_all(query, *params)
        return [self._row_to_record(row) for row in rows]

    async def review_count_summary(self, *, org_id: str) -> dict[str, Any]:
        rows = await self.database.fetch_all(
            "SELECT * FROM reviews WHERE org_id = $1 ORDER BY created_at ASC, id ASC",
            org_id,
        )
        records = [self._row_to_record(row) for row in rows]
        return {
            "total": len(records),
            "counts": review_counts_from_records(records),
        }

    async def list_reviews_page(
        self,
        *,
        status: str | None = None,
        org_id: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 200))
        rows = await self.list_reviews(
            status=status,
            org_id=org_id,
            limit=bounded_limit + 1,
            cursor=cursor,
        )
        has_more = len(rows) > bounded_limit
        items = rows[:bounded_limit]
        summary = await self.review_count_summary(org_id=org_id)
        return {
            "items": items,
            "total": summary["total"],
            "counts": summary["counts"],
            "next_cursor": _encode_cursor(items[-1]) if has_more and items else None,
        }

    async def mark_notification_sent(self, review_id: str) -> None:
        now = datetime.now(UTC)
        query = "UPDATE reviews SET notification_sent_at = $2 WHERE id = $1"
        await self.database.execute(query, review_id, now)

    async def store_interrupt(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        reason: Any,
        workflow_type: str,
        org_id: str = "",
    ) -> ReviewRecord:
        """Persist a graph review-gate interrupt as a pending review.

        Maps the strands interrupt onto the reviews schema:
        thread_id=run_id, tool_name='doc-review', and the interrupt
        identity in tool_args/metadata so the §9 resume route can find it.
        Persists a structured ``detail`` JSONB column for the frontend
        (summary/evaluation/evidence_count) instead of flattening to a string.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=24)
        review_id = str(uuid4())

        query = """
        INSERT INTO reviews (
            id, org_id, thread_id, workflow, tool_name, tool_args,
            action_description, status, created_at, expires_at, metadata, detail
        ) VALUES ($1, $2, $3, $4, 'doc-review', $5::JSONB, $6,
                  'pending', $7, $8, $9::JSONB, $10::JSONB)
        """

        tool_args = {"interrupt_id": interrupt_id}
        metadata = {
            "run_id": run_id,
            "interrupt_id": interrupt_id,
            "reason": reason if isinstance(reason, (str, int, float, bool)) else json.dumps(reason),
        }
        detail = _reason_detail(reason)
        # Prefer the summary text for action_description over a raw dict repr
        if isinstance(reason, dict):
            action_description = str(reason.get("summary") or "a documentation review is pending")
        else:
            action_description = str(reason) if reason is not None else None

        await self.database.execute(
            query,
            review_id,
            org_id,
            run_id,
            workflow_type,
            json.dumps(tool_args),
            action_description,
            now,
            expires_at,
            json.dumps(metadata),
            json.dumps(detail),
        )

        return ReviewRecord(
            id=review_id,
            org_id=org_id,
            thread_id=run_id,
            workflow=workflow_type,
            tool_name="doc-review",
            tool_args=tool_args,
            action_description=action_description,
            status="pending",
            created_at=now,
            expires_at=expires_at,
            metadata=metadata,
            detail=detail,
        )

    def _row_to_record(self, row: Any) -> ReviewRecord:
        tool_args = row.get("tool_args", "{}")
        if isinstance(tool_args, str):
            tool_args = json.loads(tool_args)
        # detail is JSONB — may be a string, dict, or None
        detail = row.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        elif detail is None:
            detail = None
        elif not isinstance(detail, dict):
            detail = {}

        # metadata may be string or dict
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        return ReviewRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            thread_id=str(row["thread_id"]),
            workflow=str(row["workflow"]),
            tool_name=str(row["tool_name"]),
            tool_args=tool_args,
            action_description=row.get("action_description"),
            status=str(row["status"]),
            reviewer_id=row.get("reviewer_id"),
            decision=row.get("decision"),
            decision_comment=row.get("decision_comment"),
            decided_at=row.get("decided_at"),
            created_at=row.get("created_at", datetime.now(UTC)),
            expires_at=row.get("expires_at"),
            notification_sent_at=row.get("notification_sent_at"),
            metadata=metadata if isinstance(metadata, dict) else {},
            detail=detail if isinstance(detail, dict) else None,
        )
