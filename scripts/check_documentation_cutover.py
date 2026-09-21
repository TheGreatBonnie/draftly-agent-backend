#!/usr/bin/env python3
"""Read-only documentation workflow cutover gate.

Exits 0 only when no documentation workflow is in a nonterminal status
(``queued``, ``running``, ``pending_review``, ``pending_intervention``).
Prints a stable operator table of the blocking runs and their statuses. The
script never updates, cancels, or pauses runs; resolve those rows through the
normal review/resume surfaces, then rerun this gate until it exits 0.
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

NONTERMINAL_STATUSES = ("queued", "running", "pending_review", "pending_intervention")

DOCUMENTATION_WORKFLOW_KEYS = {
    "github_pr",
    "github_release",
    "documentation_sync",
    "documentation_audit",
}


def _workflow_key_from_event_type(event_type: str | None) -> str | None:
    value = (event_type or "").lower()
    if value.startswith("pull_request"):
        return "github_pr"
    if value.startswith("release"):
        return "github_release"
    return None


def _is_documentation_run(row: Any) -> bool:
    data = dict(row)
    key = str(data.get("workflow_key") or "") or _workflow_key_from_event_type(
        data.get("event_type")
    )
    return key in DOCUMENTATION_WORKFLOW_KEYS


async def find_pending_documentation_runs(client: DatabaseClient) -> list[dict[str, Any]]:
    rows = await client.fetch_all(
        """
        SELECT wr.id, wr.definition_id::text, wr.event_type, wr.source, wr.status,
               wr.title, wr.repository, wr.created_at, d.workflow_key
        FROM workflow_runs wr
        LEFT JOIN workflow_definitions d ON wr.definition_id = d.id
        WHERE wr.status IN ('queued', 'running', 'pending_review', 'pending_intervention')
        ORDER BY wr.created_at DESC, wr.id DESC
        """,
    )
    return [
        dict(row)
        for row in rows
        if _is_documentation_run(row) and str(row.get("status")) in NONTERMINAL_STATUSES
    ]


def render_table(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "No nonterminal documentation workflows."
    lines = [
        f"{'RUN ID':<40} {'STATUS':<20} {'TITLE':<40} {'REPOSITORY':<40}",
        "-" * 144,
    ]
    for run in runs:
        lines.append(
            f"{str(run.get('id') or ''):<40} "
            f"{str(run.get('status') or ''):<20} "
            f"{str(run.get('title') or ''):<40} "
            f"{str(run.get('repository') or ''):<40}"
        )
    return "\n".join(lines)


def exit_code(runs: list[dict[str, Any]]) -> int:
    return 0 if not runs else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    if not os.getenv("DATABASE_URL") and not os.getenv("NEON_DATABASE_URL"):
        print("ERROR: DATABASE_URL or NEON_DATABASE_URL must be set")
        return 1

    client = DatabaseClient()
    await client.start()
    try:
        runs = await find_pending_documentation_runs(client)
    finally:
        await client.close()

    print(render_table(runs))
    code = exit_code(runs)
    if code != 0:
        print(
            "\nNonterminal documentation workflows remain. Resolve or cancel them "
            "through the review/resume surfaces, then rerun this gate."
        )
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
