"""Workflow resume/retry worker entrypoint.

Now delegates to the RQ worker. This file is kept for backwards
compatibility but simply calls the RQ worker.

Usage:
    python -m workers.workflow_worker
"""

from __future__ import annotations

from workers.rq_worker import main

if __name__ == "__main__":
    main()
