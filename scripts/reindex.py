#!/usr/bin/env python3
"""Trigger a documentation sync (reindex) for one repository."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.app.config import get_settings
from draftly.app.lifecycle import create_application
from draftly.workflows.documentation.documentation_sync import run_documentation_sync


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex documentation")
    parser.add_argument("--org-id", required=True, help="Clerk organization ID")
    parser.add_argument(
        "--repository",
        required=True,
        help="Repository full name (owner/name) to reindex",
    )
    parser.add_argument(
        "--include", nargs="*", default=None, help="Glob patterns to include"
    )
    parser.add_argument(
        "--exclude", nargs="*", default=None, help="Glob patterns to exclude"
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    print("Starting documentation reindex...")
    print(f"Org: {args.org_id}")
    print(f"Repository: {args.repository}")

    application = create_application(settings=get_settings())
    await application.startup()
    try:
        assert application.workflows is not None
        state = await run_documentation_sync(
            application.workflows.context,
            org_id=args.org_id,
            repository_full_name=args.repository,
            include=args.include,
            exclude=args.exclude,
        )
        print(f"Reindex {state.status}")
        if state.result is not None:
            print(json.dumps(state.result, indent=2, default=str))
        if state.errors:
            for error in state.errors:
                print(f"Error: {error}")
    finally:
        await application.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
