#!/usr/bin/env python3
"""Reconcile stale PR-run read-model statuses (jobs, github_workflows) against
the events rows (source of truth). Heals runs left "running" after a failed
terminal events mark or a failed job-row write."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.workflows.documentation.reconciliation import reconcile_stale_runs


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile stale PR-run statuses")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Most recent events to scan",
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    application = create_application(settings=get_settings())
    await application.startup()
    try:
        assert application.workflows is not None
        context = application.workflows.context
        reconciled = await reconcile_stale_runs(context, limit=args.limit)
        print(f"Reconciled {reconciled} runs")
    finally:
        await application.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
