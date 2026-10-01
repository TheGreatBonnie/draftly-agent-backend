"""Organization-scoped dashboard overview aggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from draftly.persistence.repositories.github import list_github_workflows_record


def _page_quality_evaluation_summary(
    page_quality: dict[str, Any],
) -> tuple[dict[str, Any], int, str]:
    """Adapt a PageQualityRepository summary to the overview snapshot shape.

    The snapshot keeps its harness-era keys (average_score, trend, dimensions)
    so the frontend needs no change; only the data source moved. Trend is the
    last scored day minus the first scored day, or None when fewer than two
    days carry scores.
    """
    by_metric = page_quality.get("by_metric") or []
    dimensions = sorted(
        (
            {"name": str(metric.get("metric")), "value": float(metric.get("average_score"))}
            for metric in by_metric
            if metric.get("metric") and metric.get("average_score") is not None
        ),
        key=lambda item: item["name"],
    )
    scored_days = [
        float(point.get("average_score"))
        for point in page_quality.get("trend") or []
        if point.get("average_score") is not None
    ]
    trend = round(scored_days[-1] - scored_days[0], 2) if len(scored_days) >= 2 else None
    needs_work = int(page_quality.get("needs_revision") or 0) + int(
        page_quality.get("awaiting_human") or 0
    )
    if int(page_quality.get("total_pages") or 0) == 0:
        status = "Unknown"
    elif needs_work:
        status = "Needs review"
    else:
        status = "Idle"
    return (
        {
            "average_score": page_quality.get("average_score"),
            "trend": trend,
            "dimensions": dimensions,
        },
        needs_work,
        status,
    )


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp(value: Any) -> str | None:
    parsed = _utc_datetime(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _normalize_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 1:
        score *= 100
    return max(0.0, min(100.0, score))


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_high_risk(review: Any) -> bool:
    detail = _value(review, "detail", {})
    if not isinstance(detail, dict):
        return False
    if _normalized_status(detail.get("risk")) in {"high", "critical"}:
        return True
    evaluation = detail.get("evaluation")
    if isinstance(evaluation, dict):
        evaluation = evaluation.get("score")
    score = _normalize_score(evaluation)
    return score is not None and score < 80


def _empty_activity(days: int, now: datetime) -> list[dict[str, Any]]:
    start = now.date() - timedelta(days=days - 1)
    return [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "created": 0,
            "updated": 0,
            "reviewed": 0,
            "published": 0,
        }
        for offset in range(days)
    ]


def _increment_activity(points: dict[str, dict[str, Any]], value: Any, category: str) -> None:
    timestamp = _utc_datetime(value)
    if timestamp is None:
        return
    point = points.get(timestamp.date().isoformat())
    if point is not None:
        point[category] += 1


def _mean(scores: list[float]) -> float | None:
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def _sort_key(record: Any, field: str) -> datetime:
    return _utc_datetime(_value(record, field)) or datetime.min.replace(tzinfo=UTC)


def _recent_changes(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from draftly.app.api.routes.documentation import derive_status

    changes: list[dict[str, Any]] = []
    recent_documents = sorted(
        documents, key=lambda item: _sort_key(item, "updated_at"), reverse=True
    )[:5]
    for document in recent_documents:
        document_id = str(document.get("id") or "")
        repository = str(document.get("repository") or "")
        path = str(document.get("path") or "")
        detail = " · ".join(part for part in (repository, path) if part)
        changes.append(
            {
                "id": document_id,
                "title": str(document.get("title") or path or document_id),
                "detail": detail,
                "timestamp": _timestamp(document.get("updated_at")),
                "status": derive_status(document.get("status")),
                "href": f"/documentation/{document_id}",
            }
        )
    return changes


def _workflow_snapshot(
    workflows: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    running_statuses = {"running", "started"}
    scheduled_statuses = {"scheduled", "queued", "pending"}
    active: list[dict[str, Any]] = []
    running = 0
    scheduled = 0
    for workflow in workflows:
        status = _normalized_status(workflow.get("status"))
        if status in running_statuses:
            running += 1
        elif status in scheduled_statuses:
            scheduled += 1
        else:
            continue
        workflow_id = str(workflow.get("run_id") or workflow.get("workflow_id") or "")
        active.append(
            {
                "id": workflow_id,
                "name": str(workflow.get("title") or workflow_id),
                "repository": str(workflow.get("repository") or workflow.get("repo") or ""),
                "status": status,
                "timestamp": _timestamp(workflow.get("created_at")),
                "href": f"/workflows/{workflow_id}",
            }
        )
    active.sort(
        key=lambda item: _utc_datetime(item["timestamp"]) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    return (
        {
            "active_workflows": running + scheduled,
            "running_workflows": running,
            "scheduled_workflows": scheduled,
        },
        active[:5],
    )


async def _integration_health(repositories: Any, database: Any, org_id: str) -> tuple[int, int]:
    connected = 0
    issues = 0

    try:
        github_installations = repositories.github_installations
        if await github_installations.list_by_org(org_id):
            connected += 1
        else:
            issues += 1
    except Exception:
        issues += 1

    try:
        if database is None:
            raise RuntimeError("database unavailable")
        slack_installations = await database.fetch_all(
            "SELECT 1 FROM slack_installations WHERE org_id = $1 LIMIT 1", org_id
        )
        if slack_installations:
            connected += 1
        else:
            issues += 1
    except Exception:
        issues += 1

    try:
        if database is None:
            raise RuntimeError("database unavailable")
        discord = await database.fetch_one(
            "SELECT discord_guild_id FROM organizations WHERE clerk_org_id = $1", org_id
        )
        if discord and discord.get("discord_guild_id"):
            connected += 1
        else:
            issues += 1
    except Exception:
        issues += 1

    return connected, issues


async def _scheduler_status(repositories: Any, org_id: str) -> str:
    jobs = getattr(repositories, "jobs", None)
    list_active = getattr(jobs, "list_active", None)
    if list_active is None:
        return "Unavailable"
    try:
        active_jobs = await list_active(org_id=org_id)
    except Exception:
        return "Unavailable"
    return "Healthy" if active_jobs else "Idle"


async def _pending_intervention_count(repositories: Any, org_id: str) -> int:
    steering = getattr(repositories, "steering_interventions", None)
    count_pending = getattr(steering, "count_pending_for_org", None)
    if count_pending is None:
        return 0
    try:
        return max(0, int(await count_pending(org_id=org_id)))
    except Exception:
        return 0


async def _agent_health(application: Any, repositories: Any, org_id: str) -> tuple[int, int]:
    agent_runs = getattr(repositories, "agent_runs", None)
    list_summaries = getattr(agent_runs, "list_agent_summaries", None)
    if list_summaries is not None:
        summaries = await list_summaries(org_id=org_id, limit=200)
        return (
            len(summaries),
            sum(item.get("last_run_status") != "failed" for item in summaries),
        )

    # Compatibility for reduced application compositions used by tests and
    # consumers that do not yet expose the bulk telemetry read model.
    from draftly.app.api.routes.agents import build_agent_summaries

    summaries = await build_agent_summaries(application, org_id=org_id)
    return (
        len(summaries),
        sum(item.get("status") != "failed" for item in summaries),
    )


async def build_overview_snapshot(application: Any, org_id: str, days: int) -> dict[str, Any]:
    """Aggregate the current organization's dashboard metrics into one snapshot."""
    repositories = application.dependencies.repositories
    from draftly.app.api.routes.documentation import derive_status

    integrations = getattr(application.dependencies, "integrations", None)
    database = getattr(integrations, "database", None)
    workflow_read = (
        list_github_workflows_record(org_id=org_id, db=database)
        if database is not None
        else asyncio.sleep(0, result=[])
    )
    (
        documents,
        pending_reviews,
        reviews,
        page_quality,
        workflows,
        integration_health,
        scheduler_status,
        pending_interventions,
        agent_health,
    ) = await asyncio.gather(
        repositories.documents.list_by_org(org_id=org_id, limit=1000),
        repositories.reviews.list_reviews(status="pending", org_id=org_id, limit=200),
        repositories.reviews.list_reviews(status=None, org_id=org_id, limit=1000),
        repositories.page_quality.summary(org_id, days),
        workflow_read,
        _integration_health(repositories, database, org_id),
        _scheduler_status(repositories, org_id),
        _pending_intervention_count(repositories, org_id),
        _agent_health(application, repositories, org_id),
    )
    data_sources_connected, integration_issues = integration_health
    agent_count, agents_online = agent_health

    evaluation, failed_evaluations, evaluations_status = _page_quality_evaluation_summary(
        page_quality
    )
    workflow_summary, active_workflows = _workflow_snapshot(workflows)
    now = datetime.now(UTC)
    activity = _empty_activity(days, now)
    activity_by_day = {point["date"]: point for point in activity}
    for document in documents:
        created_at = document.get("created_at")
        updated_at = document.get("updated_at")
        _increment_activity(activity_by_day, created_at, "created")
        if _utc_datetime(updated_at) != _utc_datetime(created_at):
            _increment_activity(activity_by_day, updated_at, "updated")
        if derive_status(document.get("status")) == "published":
            _increment_activity(activity_by_day, updated_at, "published")
    for review in reviews:
        _increment_activity(
            activity_by_day,
            _value(review, "decided_at") or _value(review, "completed_at"),
            "reviewed",
        )

    return {
        "summary": {
            "documentation_total": len(documents),
            **workflow_summary,
            "pending_reviews": len(pending_reviews),
            "average_evaluation_score": evaluation["average_score"],
        },
        "attention": {
            "pending_reviews": len(pending_reviews),
            "pending_interventions": pending_interventions,
            "high_risk_reviews": sum(_is_high_risk(review) for review in pending_reviews),
            "failed_evaluations": failed_evaluations,
            "integration_issues": integration_issues,
            "stale_documentation": sum(bool(document.get("stale")) for document in documents),
        },
        "system": {
            "agents_online": agents_online,
            "agents_total": agent_count,
            "data_sources_connected": data_sources_connected,
            "data_sources_total": 3,
            "evaluations_status": evaluations_status,
            "scheduler_status": scheduler_status,
        },
        "recent_changes": _recent_changes(documents),
        "active_workflows": active_workflows,
        "evaluation": evaluation,
        "activity": activity,
    }
