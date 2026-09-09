"""Organization-scoped dashboard overview aggregation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from draftly.persistence.repositories.github import list_github_workflows_record

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


def _increment_activity(
    points: dict[str, dict[str, Any]], value: Any, category: str
) -> None:
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


def _evaluation_summary(evaluations: list[Any]) -> tuple[dict[str, Any], int, str]:
    ordered = sorted(evaluations, key=lambda item: _sort_key(item, "created_at"), reverse=True)
    scores = [
        score
        for item in ordered
        if (score := _normalize_score(_value(item, "score"))) is not None
    ]
    average = _mean(scores)
    half = len(scores) // 2
    trend = None
    if half:
        latest = _mean(scores[:half])
        previous = _mean(scores[half : half * 2])
        if latest is not None and previous is not None:
            trend = round(latest - previous, 2)

    dimensions: dict[str, list[float]] = defaultdict(list)
    for evaluation in ordered:
        metrics = _value(evaluation, "metrics", {})
        granular = metrics.get("granular", []) if isinstance(metrics, dict) else []
        for item in granular if isinstance(granular, list) else []:
            if not isinstance(item, dict) or not item.get("metric"):
                continue
            score = _normalize_score(item.get("score"))
            if score is not None:
                dimensions[str(item["metric"])].append(score)

    statuses = [_normalized_status(_value(item, "status")) for item in ordered]
    if any(status in {"running", "queued"} for status in statuses):
        status = "Running"
    elif statuses and statuses[0] == "failed":
        status = "Failed"
    elif statuses:
        status = "Idle"
    else:
        status = "Unknown"

    return (
        {
            "average_score": average,
            "trend": trend,
            "dimensions": [
                {"name": name, "value": _mean(values)}
                for name, values in sorted(dimensions.items())
            ],
        },
        sum(status == "failed" for status in statuses),
        status,
    )


def _recent_changes(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from draftly.app.api.routes.documentation import derive_status

    changes: list[dict[str, Any]] = []
    for document in sorted(documents, key=lambda item: _sort_key(item, "updated_at"), reverse=True)[:5]:
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


def _workflow_snapshot(workflows: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, Any]]]:
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


async def _integration_health(
    repositories: Any, database: Any, org_id: str
) -> tuple[int, int]:
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


async def build_overview_snapshot(
    application: Any, org_id: str, days: int
) -> dict[str, Any]:
    """Aggregate the current organization's dashboard metrics into one snapshot."""
    repositories = application.dependencies.repositories
    from draftly.app.api.routes.agents import build_agent_summaries
    from draftly.app.api.routes.documentation import derive_status

    documents = await repositories.documents.list_by_org(org_id=org_id, limit=1000)
    pending_reviews = await repositories.reviews.list_reviews(
        status="pending", org_id=org_id, limit=200
    )
    reviews = await repositories.reviews.list_reviews(status=None, org_id=org_id, limit=1000)
    evaluations = await repositories.evaluations.search(
        org_id=org_id, evaluation_type=None, limit=100
    )
    integrations = getattr(application.dependencies, "integrations", None)
    database = getattr(integrations, "database", None)
    workflows = (
        await list_github_workflows_record(org_id=org_id, db=database)
        if database is not None
        else []
    )
    data_sources_connected, integration_issues = await _integration_health(
        repositories, database, org_id
    )

    evaluation, failed_evaluations, evaluations_status = _evaluation_summary(evaluations)
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

    agent_summaries = await build_agent_summaries(application, org_id=org_id)
    agent_count = len(agent_summaries)
    agents_online = sum(item.get("status") != "failed" for item in agent_summaries)
    return {
        "summary": {
            "documentation_total": len(documents),
            **workflow_summary,
            "pending_reviews": len(pending_reviews),
            "average_evaluation_score": evaluation["average_score"],
        },
        "attention": {
            "pending_reviews": len(pending_reviews),
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
            "scheduler_status": "Unavailable",
        },
        "recent_changes": _recent_changes(documents),
        "active_workflows": active_workflows,
        "evaluation": evaluation,
        "activity": activity,
    }
