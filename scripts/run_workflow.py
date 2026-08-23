#!/usr/bin/env python3
"""Run a registered workflow by name with a JSON kwargs payload."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Draftly workflow")
    parser.add_argument(
        "--workflow",
        required=True,
        help="Workflow name (e.g., documentation_sync, github_pull_request)",
    )
    parser.add_argument(
        "--payload",
        default="{}",
        help="JSON object with keyword arguments for the workflow",
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON payload: {e}")
        return 1

    if not isinstance(payload, dict):
        print("ERROR: Payload must be a JSON object")
        return 1

    print(f"Running workflow: {args.workflow}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    application = create_application(settings=get_settings())
    await application.startup()
    try:
        assert application.workflows is not None
        registry = application.workflows.registry
        workflow = registry.get(args.workflow)
        if workflow is None:
            print(f"ERROR: Workflow '{args.workflow}' not found")
            print(f"Available workflows: {', '.join(registry.names())}")
            return 1

        state = await workflow(application.workflows.context, **payload)
        status = getattr(state, "status", "unknown")
        result = getattr(state, "result", None)
        print(f"Workflow finished: {status}")
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
    finally:
        await application.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
