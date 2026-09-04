#!/usr/bin/env python3
"""Run the documentation evaluation loop over golden datasets."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.workflows.evaluation.documentation_evaluation import run_evaluation_loop

load_dotenv()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run documentation evaluation")
    parser.add_argument(
        "--datasets",
        default=None,
        help="JSON file with a list of dataset dicts (default: load configured datasets)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Enable live evaluation with real agent invocation and "
            "LLM-judge evaluators (requires model keys)"
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        print("ERROR: DATABASE_URL not set (expected in .env or environment)")
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
    if args.live:
        print("LIVE MODE: real agent invocation + LLM judges enabled")
        print("HINT: live runs 3 datasets x ~600s inner budget; use --datasets")
        print("with a single-case file for smoke runs to stay within timeouts")
    else:
        print("SYNC MODE (default): deterministic offline checks, safe for CI")

    application = create_application(settings=settings)
    await application.startup()
    try:
        assert application.workflows is not None
        state = await run_evaluation_loop(
            application.workflows.context,
            datasets=datasets,
            live=args.live,
        )
        print(f"Evaluation finished: {state.status}")
        if state.result is not None:
            print(json.dumps(state.result, indent=2, default=str))
        if state.errors:
            for error in state.errors:
                print(f"  error: {error}")
    finally:
        await application.shutdown()
        # Let cancelled aiohttp connectors finish closing before
        # asyncio.run() tears down the loop (avoids "Unclosed client session").
        await asyncio.sleep(0.25)

    print("Evaluation batch complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
