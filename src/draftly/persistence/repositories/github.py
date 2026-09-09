"""GitHub repository operations."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient

_DASHBOARD_PAGE_SIZE = 100


class GitHubWorkflowRepository:
    """Persistence adapter for the workflow identity/read-model row."""

    def __init__(self, db: DatabaseClient) -> None:
        self.db = db

    async def update_status(self, *, workflow_id: str, status: str) -> None:
        await update_github_workflow_status(
            workflow_id=workflow_id,
            status=status,
            db=self.db,
        )


class GitHubInstallationsRepository:
    """Org-scoped access to github_installations for workflow contexts."""

    def __init__(self, db: DatabaseClient | None = None) -> None:
        self.db = db or DatabaseClient()

    async def list_by_org(self, org_id: str) -> list[dict[str, Any]]:
        """List installations belonging to one org (clerk_org_id)."""
        rows = await self.db.fetch_all(
            """
            SELECT gi.id::text, gi.installation_id, gi.github_org, gi.repositories,
                   gi.created_at, gi.updated_at
            FROM github_installations gi
            WHERE gi.org_id = $1
            ORDER BY gi.created_at DESC
            """,
            org_id,
        )
        result = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("repositories"), str):
                d["repositories"] = json.loads(d["repositories"])
            result.append(d)
        return result

    async def first_for_org(self, org_id: str) -> dict[str, Any] | None:
        """Most recent installation for an org, or None."""
        installs = await self.list_by_org(org_id)
        return installs[0] if installs else None


async def get_org_by_github_org(
    *,
    github_org: str,
    db: DatabaseClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by GitHub org name."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        "SELECT clerk_org_id, clerk_org_name, github_org FROM organizations WHERE github_org = $1",
        github_org,
    )
    if not row:
        return None
    return {
        "clerk_org_id": row["clerk_org_id"],
        "clerk_org_name": row["clerk_org_name"],
        "github_org": row["github_org"],
    }


async def store_github_installation(
    *,
    org_id: str,
    installation_id: int,
    github_org: str,
    repositories: list[dict[str, Any]] | None = None,
    db: DatabaseClient | None = None,
) -> str:
    """Store or update a GitHub App installation."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    existing = await db.fetch_one(
        "SELECT id::text FROM github_installations WHERE installation_id = $1",
        installation_id,
    )

    if existing:
        await db.execute(
            """UPDATE github_installations
               SET repositories = $1, updated_at = now()
               WHERE installation_id = $2""",
            json.dumps(repositories or []),
            installation_id,
        )
        return cast(str, existing["id"])

    row = await db.fetch_one(
        """INSERT INTO github_installations (org_id, installation_id, github_org, repositories)
           VALUES ($1, $2, $3, $4) RETURNING id::text""",
        org_id,
        installation_id,
        github_org,
        json.dumps(repositories or []),
    )
    if row is None:
        raise RuntimeError("github installation row missing after insert")
    return cast(str, row["id"])


async def remove_github_installation(
    *,
    installation_id: int,
    db: DatabaseClient | None = None,
) -> None:
    """Delete a GitHub App installation record."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    await db.execute(
        "DELETE FROM github_installations WHERE installation_id = $1",
        installation_id,
    )


async def list_github_installations(
    *,
    db: DatabaseClient | None = None,
) -> list[dict[str, Any]]:
    """List all GitHub App installations with org names."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    rows = await db.fetch_all(
        """SELECT gi.id::text, gi.installation_id, gi.github_org, gi.repositories,
                  gi.created_at, gi.updated_at, o.clerk_org_name as org_name
           FROM github_installations gi
           JOIN organizations o ON o.clerk_org_id = gi.org_id
           ORDER BY gi.created_at DESC"""
    )
    result = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("repositories"), str):
            d["repositories"] = json.loads(d["repositories"])
        result.append(d)
    return result


async def store_github_workflow(
    *,
    org_id: str,
    workflow_id: str,
    installation_id: int,
    owner: str,
    repo: str,
    issue_number: int,
    run_id: str,
    title: str = "",
    actor: str = "",
    db: DatabaseClient | None = None,
) -> str:
    """Store a GitHub workflow for tracking."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """INSERT INTO github_workflows
           (org_id, workflow_id, installation_id, owner, repo, issue_number, run_id, title, actor)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id::text""",
        org_id,
        workflow_id,
        installation_id,
        owner,
        repo,
        issue_number,
        run_id,
        title,
        actor,
    )
    if row is None:
        raise RuntimeError("github workflow row missing after insert")
    return cast(str, row["id"])


async def save_github_workflow(
    *,
    org_id: str,
    workflow_id: str,
    installation_id: int,
    owner: str,
    repo: str,
    issue_number: int,
    run_id: str,
    title: str = "",
    actor: str = "",
    event_type: str = "",
    db: DatabaseClient | None = None,
) -> str:
    """Save or update a GitHub workflow status."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """INSERT INTO github_workflows
           (org_id, workflow_id, installation_id, owner, repo, issue_number,
            run_id, title, actor, event_type, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending')
           ON CONFLICT (workflow_id) DO UPDATE SET
               org_id = EXCLUDED.org_id,
               installation_id = EXCLUDED.installation_id,
               owner = EXCLUDED.owner,
               repo = EXCLUDED.repo,
               issue_number = EXCLUDED.issue_number,
               status = EXCLUDED.status,
               run_id = EXCLUDED.run_id,
               title = EXCLUDED.title,
               actor = EXCLUDED.actor,
               event_type = EXCLUDED.event_type,
               updated_at = now()
           RETURNING id::text""",
        org_id,
        workflow_id,
        installation_id,
        owner,
        repo,
        issue_number,
        run_id,
        title,
        actor,
        event_type,
    )
    if row is None:
        raise RuntimeError("github workflow row missing after insert")
    return cast(str, row["id"])


async def get_github_workflow_by_issue(
    *,
    owner: str,
    repo: str,
    issue_number: int,
    db: DatabaseClient | None = None,
) -> dict[str, Any] | None:
    """Get workflow by GitHub issue identifiers."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """SELECT id::text, workflow_id, installation_id, owner, repo, issue_number, status,
                  run_id, title, actor
           FROM github_workflows
           WHERE owner = $1 AND repo = $2 AND issue_number = $3
           ORDER BY created_at DESC LIMIT 1""",
        owner,
        repo,
        issue_number,
    )
    return dict(row) if row else None


async def get_github_workflow_by_run_id(
    *,
    run_id: str,
    db: DatabaseClient | None = None,
) -> dict[str, Any] | None:
    """Get workflow by run_id (github_workflows.run_id or workflow_id)."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    row = await db.fetch_one(
        """SELECT id::text, workflow_id, installation_id, owner, repo, issue_number, status,
                  run_id, title, actor
           FROM github_workflows
           WHERE run_id = $1 OR workflow_id = $1
           ORDER BY created_at DESC LIMIT 1""",
        run_id,
    )
    return dict(row) if row else None


async def list_github_workflows_record(
    *,
    org_id: str,
    db: DatabaseClient | None = None,
    limit: int = _DASHBOARD_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """List GitHub workflows with joined state for the frontend list page."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    # Fetch all github_workflows for the org
    gw_rows = await db.fetch_all(
        """SELECT workflow_id, run_id, title, owner, repo, issue_number,
                  actor, event_type, status, created_at
           FROM github_workflows
           WHERE org_id = $1
           ORDER BY created_at DESC LIMIT $2""",
        org_id,
        limit,
    )

    if not gw_rows:
        return []

    # Collect all run_ids to fetch jobs and workflow_events in bulk
    run_ids = [str(r["run_id"]) for r in gw_rows if r.get("run_id")][:limit]
    if not run_ids:
        jobs_map: dict[str, dict] = {}
        events_by_run: dict[str, list[dict]] = {}
    else:
        # Fetch jobs for all runs
        jobs_rows = await db.fetch_all(
            """SELECT run_id, status, started_at, completed_at
               FROM jobs
               WHERE run_id = ANY($1::TEXT[])""",
            run_ids,
        )
        jobs_map = {str(r["run_id"]): r for r in jobs_rows}

        # Lightweight event listing: only routing/minimal columns. The dashboard
        # polls this endpoint continuously, so the fat JSON `payload` column must
        # not be transferred for every event — only the event types that actually
        # consume it (workflow_result status, node_stop status) fetch payloads,
        # in a separate targeted query.
        event_rows = await db.fetch_all(
            """SELECT run_id, seq, type, node_id
               FROM workflow_events
               WHERE run_id = ANY($1::TEXT[])""",
            run_ids,
        )
        payload_rows = await db.fetch_all(
            """SELECT run_id, seq, type, node_id, payload
               FROM workflow_events
               WHERE run_id = ANY($1::TEXT[])
                 AND type IN ('workflow_result', 'node_stop')""",
            run_ids,
        )
        payloads_by_key = {
            (str(r["run_id"]), r["seq"]): r.get("payload") for r in payload_rows
        }

        events_by_run = {}
        for r in event_rows:
            rid = str(r["run_id"])
            event = dict(r)
            payload = payloads_by_key.get((rid, r["seq"]))
            if payload is not None:
                event["payload"] = payload
            events_by_run.setdefault(rid, []).append(event)

    result = []
    for gw in gw_rows:
        run_id = str(gw.get("run_id") or "")
        job = jobs_map.get(run_id)
        events = events_by_run.get(run_id, [])
        event_type = str(gw.get("event_type") or "")
        stage_order = (
            ["classify", "context", "research", "impact", "changelog", "evaluate", "deliver"]
            if event_type.startswith("release.")
            else [
                "classify", "context", "research", "impact", "answer",
                "update", "create", "evaluate", "deliver",
            ]
        )

        # Determine status: prefer workflow_result, else jobs.status, else gw.status
        terminal_status = None
        for ev in events:
            if ev.get("type") == "workflow_result" and ev.get("payload"):
                payload = ev.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                if isinstance(payload, dict):
                    terminal_status = payload.get("status")
                    if terminal_status:
                        break

        if terminal_status:
            if terminal_status == "COMPLETED":
                status = "completed"
            elif terminal_status == "FAILED":
                status = "failed"
            else:
                status = terminal_status.lower()
        elif job and job.get("status"):
            status = job["status"]
        else:
            status = gw.get("status", "pending")

        # Determine current_stage and stages from node_states in events
        # Replicate frontend use-workflow-events.ts scanning: seq-ordered events
        # node_start -> running, node_stop -> done/failed, else keep running
        node_states: dict[str, str] = {}  # node_id -> done|running|failed
        # Ensure events are sorted by seq if present
        try:
            events_sorted = sorted(events, key=lambda e: int(e.get("seq") or 0))
        except Exception:
            events_sorted = events
        for ev in events_sorted:
            node_id = ev.get("node_id")
            if not node_id:
                continue
            ev_type = ev.get("type")
            if ev_type == "node_start":
                node_states[str(node_id)] = "running"
            elif ev_type == "node_stop":
                payload = ev.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                if isinstance(payload, dict) and payload.get("status") == "COMPLETED":
                    node_states[str(node_id)] = "done"
                else:
                    node_states[str(node_id)] = "failed"
            elif ev_type == "workflow_result":
                # workflow_result does not map to a node, but if needed mark deliver
                pass

        # Build stages list in topological order
        stages = []
        for sid in stage_order:
            state = node_states.get(sid, "queued")
            if state == "queued" and any(s != "queued" for s in node_states.values()):
                idx = stage_order.index(sid)
                if idx > 0 and all(node_states.get(stage_order[i]) == "done" for i in range(idx)):
                    state = "waiting"
            stages.append(state)

        # current_stage = first running ?? last done ?? first waiting
        current_stage = None
        # first running
        for sid, st in zip(stage_order, stages):
            if st == "running":
                current_stage = sid
                break
        if current_stage is None:
            # last done
            for sid, st in reversed(list(zip(stage_order, stages))):
                if st == "done":
                    current_stage = sid
                    break
        if current_stage is None:
            for sid, st in zip(stage_order, stages):
                if st == "waiting":
                    current_stage = sid
                    break

        # Time duration
        time_str = "—"
        if job and job.get("started_at"):
            from datetime import datetime
            start = job["started_at"]
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end = job.get("completed_at")
            if end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(end.replace("Z", "+00:00"))
            else:
                end = datetime.now(start.tzinfo or UTC)
            delta = end - start
            if delta.total_seconds() < 60:
                time_str = f"{int(delta.total_seconds())}s"
            elif delta.total_seconds() < 3600:
                m = int(delta.total_seconds() // 60)
                s = int(delta.total_seconds() % 60)
                time_str = f"{m}m {s}s"
            else:
                h = int(delta.total_seconds() // 3600)
                m = int((delta.total_seconds() % 3600) // 60)
                time_str = f"{h}h {m}m"

        if event_type.startswith("release."):
            trigger = gw.get("title") or "Release"
        elif event_type.startswith("push."):
            trigger = "Push"
        else:
            trigger = f"PR #{gw.get('issue_number', 0)}" if gw.get("issue_number") else "Unknown"
        result.append({
            "run_id": run_id,
            "title": gw.get("title", ""),
            "target_doc": None,  # PR runs don't have a target doc
            "trigger_label": trigger,
            "status": status,
            "current_stage": current_stage,
            "stages": stages,
            "current_stage_color": _stage_color(current_stage, node_states),
            "time": time_str,
        })

    return result


def _stage_color(stage: str | None, node_states: dict[str, str]) -> str:
    """Return color for a stage based on its state."""
    if not stage:
        return "slate"
    state = node_states.get(stage, "queued")
    if state == "running":
        return "blue"
    elif state == "done":
        return "emerald"
    elif state == "failed":
        return "red"
    return "slate"


async def update_github_workflow_status(
    *,
    workflow_id: str,
    status: str,
    db: DatabaseClient | None = None,
) -> None:
    """Update workflow status."""
    if db is None:
        from draftly.app.config import get_settings
        from draftly.app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.database

    await db.execute(
        """UPDATE github_workflows
           SET status = $1,
               completed_at = CASE
                   WHEN $1 IN ('completed', 'failed') THEN now()
                   ELSE completed_at
               END
           WHERE workflow_id = $2""",
        status,
        workflow_id,
    )
