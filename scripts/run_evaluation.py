#!/usr/bin/env python3
"""Run evaluation batch (like evaluation_worker without --watch)."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.composition import build_dependencies
from draftly.workflows.evaluation.documentation_evaluation import DocumentationEvaluationWorkflow
from draftly.workflows.evaluation.support_evaluation import SupportEvaluationWorkflow


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run evaluation batch")
    parser.add_argument(
        "--type",
        choices=["documentation", "support", "all"],
        default="all",
        help="Evaluation type",
    )
    parser.add_argument("--project-id", help="Specific project ID (optional)")
    parser.add_argument("--run-id", help="Specific run ID to evaluate (optional)")
    parser.add_argument("--limit", type=int, default=50, help="Max items to evaluate")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    deps = await build_dependencies()

    if args.type in ("documentation", "all"):
        print("Running documentation evaluation...")
        doc_workflow = DocumentationEvaluationWorkflow(deps)
        run_id = await doc_workflow.run(
            {
                "project_id": args.project_id,
                "run_id": args.run_id,
                "limit": args.limit,
            }
        )
        print(f"Documentation evaluation started: {run_id}")

        while True:
            run = await deps.jobs_store.get_run(run_id)
            if not run:
                break
            if run.status in ("completed", "failed", "cancelled"):
                print(f"  {run.status}: {run.result or run.error}")
                break
            await asyncio.sleep(3)

    if args.type in ("support", "all"):
        print("Running support evaluation...")
        support_workflow = SupportEvaluationWorkflow(deps)
        run_id = await support_workflow.run(
            {
                "project_id": args.project_id,
                "limit": args.limit,
            }
        )
        print(f"Support evaluation started: {run_id}")

        while True:
            run = await deps.jobs_store.get_run(run_id)
            if not run:
                break
            if run.status in ("completed", "failed", "cancelled"):
                print(f"  {run.status}: {run.result or run.error}")
                break
            await asyncio.sleep(3)

    print("Evaluation batch complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
