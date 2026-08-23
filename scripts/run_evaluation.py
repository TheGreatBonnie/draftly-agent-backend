#!/usr/bin/env python3
"""Run the documentation evaluation loop over golden datasets."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.workflows.evaluation.documentation_evaluation import run_evaluation_loop


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run documentation evaluation")
    parser.add_argument(
        "--datasets",
        default=None,
        help="JSON file with a list of dataset dicts (default: load configured datasets)",
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    datasets = None
    if args.datasets:
        try:
            with open(args.datasets) as f:
                datasets = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: Cannot load datasets file: {e}")
            return 1

    print("Running documentation evaluation loop...")

    application = create_application(settings=get_settings())
    await application.startup()
    try:
        assert application.workflows is not None
        state = await run_evaluation_loop(
            application.workflows.context,
            datasets=datasets,
        )
        print(f"Evaluation finished: {state.status}")
        if state.result is not None:
            print(json.dumps(state.result, indent=2, default=str))
        if state.errors:
            for error in state.errors:
                print(f"  error: {error}")
    finally:
        await application.shutdown()

    print("Evaluation batch complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
