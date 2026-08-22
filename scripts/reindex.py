#!/usr/bin/env python3
"""Trigger documentation reindexing for a project or all projects."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.composition import build_dependencies
from draftly.workflows.documentation.documentation_sync import DocumentationSyncWorkflow


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex documentation")
    parser.add_argument("--project-id", help="Specific project ID to reindex (default: all)")
    parser.add_argument(
        "--force", action="store_true", help="Force full reindex (ignore timestamps)"
    )
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for processing")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    deps = await build_dependencies()
    workflow = DocumentationSyncWorkflow(deps)

    print("Starting documentation reindex...")
    if args.project_id:
        print(f"Project: {args.project_id}")
    else:
        print("All projects")

    payload = {
        "project_id": args.project_id,
        "force": args.force,
        "batch_size": args.batch_size,
    }

    run_id = await workflow.run(payload)
    print(f"Reindex started with run_id: {run_id}")

    print("Waiting for completion...")
    while True:
        run = await deps.jobs_store.get_run(run_id)
        if not run:
            print("Run not found")
            break
        if run.status in ("completed", "failed", "cancelled"):
            print(f"Reindex {run.status}")
            if run.result:
                print(f"Result: {json.dumps(run.result, indent=2)}")
            if run.error:
                print(f"Error: {run.error}")
            break
        await asyncio.sleep(5)

    return 0


if __name__ == "__main__":
    import json

    sys.exit(asyncio.run(main()))
