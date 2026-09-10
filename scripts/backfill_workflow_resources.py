#!/usr/bin/env python3
"""Idempotently backfill canonical workflow definitions and run instances.

The script only creates/reconciles canonical rows; provider/audit tables are
never deleted. Run it once with ``--dry-run`` in every environment, inspect
the counters, then repeat with ``--apply``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.integrations.database.client import DatabaseClient


def _workflow_key(event_type: str | None) -> str:
    value = (event_type or "").lower()
    if value.startswith("pull_request"):
        return "github_pr"
    if value.startswith("release"):
        return "github_release"
    if value.startswith("issues") or value.startswith("issue"):
        return "github_issue"
    if value.startswith("slack"):
        return "slack_support"
    if value.startswith("discord"):
        return "discord_support"
    return "github_pr"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report candidates without writing")
    mode.add_argument("--apply", action="store_true", help="Apply the idempotent backfill")
    parser.add_argument("--org-id", help="Limit the backfill to one Clerk organization")
    return parser.parse_args()


async def _count(client: DatabaseClient, table: str, org_id: str | None) -> int:
    where = " WHERE org_id = $1" if org_id else ""
    row = await client.fetch_one(
        f"SELECT count(*) AS count FROM {table}{where}", *([org_id] if org_id else [])
    )
    return int((dict(row) if row else {}).get("count", 0))


async def _backfill(client: DatabaseClient, *, org_id: str | None, apply: bool) -> dict[str, int]:
    scope = " AND gw.org_id = $1" if org_id else ""
    args: tuple[Any, ...] = (org_id,) if org_id else ()
    candidates = await client.fetch_all(
        f"""
        SELECT DISTINCT gw.org_id, gw.event_type
        FROM github_workflows gw
        WHERE gw.org_id IS NOT NULL{scope}
        """,
        *args,
    )
    counters = {"definitions_created": 0, "runs_created": 0, "runs_reconciled": 0, "failed": 0}
    if not apply:
        counters["definitions_created"] = len(candidates)
        counters["runs_created"] = await _count(client, "github_workflows", org_id)
        counters["runs_reconciled"] = await _count(client, "agent_runs", org_id)
        return counters

    for candidate in candidates:
        row = dict(candidate)
        workflow_key = _workflow_key(row.get("event_type"))
        slug = f"imported-{workflow_key.replace('_', '-')}"
        try:
            await client.execute(
                """
                INSERT INTO workflow_definitions
                    (org_id, slug, name, workflow_key, status, trigger_config)
                VALUES ($1, $2, $3, $4, 'active', $5::JSONB)
                ON CONFLICT (org_id, slug) DO NOTHING
                """,
                row["org_id"],
                slug,
                f"Imported {workflow_key.replace('_', ' ').title()}",
                workflow_key,
                '{"source":"backfill"}',
            )
            counters["definitions_created"] += 1
        except Exception:
            counters["failed"] += 1

    workflows = await client.fetch_all(
        f"""
        SELECT gw.org_id, COALESCE(NULLIF(gw.run_id, ''), gw.workflow_id) AS run_id,
               gw.workflow_id AS source_event_id, gw.title, gw.owner, gw.repo, gw.actor,
               gw.event_type, gw.status
        FROM github_workflows gw
        WHERE gw.org_id IS NOT NULL{scope}
        """,
        *args,
    )
    for raw in workflows:
        row = dict(raw)
        try:
            definition = await client.fetch_one(
                """
                SELECT id::text FROM workflow_definitions
                WHERE org_id = $1 AND workflow_key = $2
                ORDER BY updated_at DESC LIMIT 1
                """,
                row["org_id"],
                _workflow_key(row.get("event_type")),
            )
            await client.execute(
                """
                INSERT INTO workflow_runs
                    (id, definition_id, org_id, source, source_event_id, event_type,
                     title, repository, actor, status, created_at, updated_at)
                VALUES ($1, $2::UUID, $3, 'github', $4, $5, $6, $7, $8,
                        CASE WHEN $9 IN (
                            'completed', 'failed', 'cancelled', 'skipped'
                        ) THEN $9 ELSE 'running' END,
                        now(), now())
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = now()
                """,
                row["run_id"],
                definition["id"] if definition else None,
                row["org_id"],
                row.get("source_event_id"),
                row.get("event_type"),
                row.get("title"),
                f"{row.get('owner')}/{row.get('repo')}"
                if row.get("owner") and row.get("repo")
                else None,
                row.get("actor"),
                row.get("status") or "running",
            )
            counters["runs_created"] += 1
        except Exception:
            counters["failed"] += 1
    return counters


async def main() -> int:
    args = _args()
    if not os.getenv("DATABASE_URL") and not os.getenv("NEON_DATABASE_URL"):
        print("ERROR: DATABASE_URL or NEON_DATABASE_URL must be set")
        return 1
    client = DatabaseClient()
    await client.start()
    try:
        counters = await _backfill(client, org_id=args.org_id, apply=args.apply)
    finally:
        await client.close()
    print("Backfill " + ("applied" if args.apply else "dry-run") + ":")
    for key, value in counters.items():
        print(f"  {key}: {value}")
    return 0 if counters["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
