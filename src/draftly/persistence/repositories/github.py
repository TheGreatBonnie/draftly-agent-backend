"""GitHub repository operations."""
from __future__ import annotations

import json
from typing import Any, cast

from integrations.cockroachdb.client import CockroachDBClient


async def get_org_by_github_org(
    *,
    github_org: str,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Find organization by GitHub org name."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

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
    db: CockroachDBClient | None = None,
) -> str:
    """Store or update a GitHub App installation."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

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
    db: CockroachDBClient | None = None,
) -> None:
    """Delete a GitHub App installation record."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    await db.execute(
        "DELETE FROM github_installations WHERE installation_id = $1",
        installation_id,
    )


async def list_github_installations(
    *,
    db: CockroachDBClient | None = None,
) -> list[dict[str, Any]]:
    """List all GitHub App installations with org names."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

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
    db: CockroachDBClient | None = None,
) -> str:
    """Store a GitHub workflow for tracking."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        """INSERT INTO github_workflows
           (org_id, workflow_id, installation_id, owner, repo, issue_number)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id::text""",
        org_id,
        workflow_id,
        installation_id,
        owner,
        repo,
        issue_number,
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
    db: CockroachDBClient | None = None,
) -> str:
    """Save or update a GitHub workflow status."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        """INSERT INTO github_workflows
           (org_id, workflow_id, installation_id, owner, repo, issue_number, status)
           VALUES ($1, $2, $3, $4, $5, $6, 'pending')
           ON CONFLICT (workflow_id) DO UPDATE SET
               status = EXCLUDED.status,
               updated_at = now()
           RETURNING id::text""",
        org_id,
        workflow_id,
        installation_id,
        owner,
        repo,
        issue_number,
    )
    if row is None:
        raise RuntimeError("github workflow row missing after insert")
    return cast(str, row["id"])


async def get_github_workflow_by_issue(
    *,
    owner: str,
    repo: str,
    issue_number: int,
    db: CockroachDBClient | None = None,
) -> dict[str, Any] | None:
    """Get workflow by GitHub issue identifiers."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

    row = await db.fetch_one(
        """SELECT id::text, workflow_id, installation_id, owner, repo, issue_number, status
           FROM github_workflows
           WHERE owner = $1 AND repo = $2 AND issue_number = $3
           ORDER BY created_at DESC LIMIT 1""",
        owner,
        repo,
        issue_number,
    )
    return dict(row) if row else None


async def update_github_workflow_status(
    *,
    workflow_id: str,
    status: str,
    db: CockroachDBClient | None = None,
) -> None:
    """Update workflow status."""
    if db is None:
        from app.config import get_settings
        from app.dependencies import build_dependencies

        settings = get_settings()
        deps = build_dependencies(settings=settings)
        db = deps.integrations.cockroachdb

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
