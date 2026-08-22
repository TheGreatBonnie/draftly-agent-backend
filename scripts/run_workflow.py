#!/usr/bin/env python3
"""Run a workflow by name with JSON payload."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.composition import build_dependencies
from draftly.workflows.registry import WorkflowRegistry


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Draftly workflow")
    parser.add_argument(
        "--workflow",
        required=True,
        help="Workflow name (e.g., documentation_sync, github_pr_workflow)",
    )
    parser.add_argument("--payload", required=True, help="JSON payload for the workflow")
    parser.add_argument("--watch", action="store_true", help="Watch for workflow completion")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON payload: {e}")
        return 1

    print(f"Running workflow: {args.workflow}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    deps = await build_dependencies()
    registry = WorkflowRegistry(deps)

    workflow = registry.get(args.workflow)
    if not workflow:
        print(f"ERROR: Workflow '{args.workflow}' not found")
        print(f"Available workflows: {', '.join(registry.list())}")
        return 1

    run_id = await workflow.run(payload)
    print(f"Workflow started with run_id: {run_id}")

    if args.watch:
        print("Waiting for completion...")
        while True:
            run = await deps.jobs_store.get_run(run_id)
            if not run:
                print("Run not found")
                break
            if run.status in ("completed", "failed", "cancelled"):
                print(f"Workflow {run.status}: {run.result or run.error}")
                break
            await asyncio.sleep(2)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
