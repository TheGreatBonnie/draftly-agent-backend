"""Audit hook upserts a jobs row on run_start so stream-ticket resolves."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.orchestration.hooks.audit import RunAuditLogger


class RecordingJobs:
    """Strict fake mirroring DatabaseJobsStore.upsert_on_conflict's contract."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upsert_on_conflict(
        self,
        *,
        run_id: str,
        org_id: str,
        name: str,
        job_type: str,
        schedule: str,
        configuration: dict,
        status: str = "pending",
    ) -> dict | None:
        self.calls.append(
            {
                "run_id": run_id,
                "org_id": org_id,
                "name": name,
                "job_type": job_type,
                "schedule": schedule,
                "configuration": configuration,
                "status": status,
            }
        )
        return self.calls[-1]


def _start_event():
    return SimpleNamespace(
        invocation_state={
            "run_id": "evt-1",
            "project_id": "org-1",
            "source": "github",
            "event_type": "pull_request.opened",
        }
    )


def test_run_start_upserts_jobs_row() -> None:
    import asyncio

    async def _run() -> None:
        jobs = RecordingJobs()
        logger = RunAuditLogger(audit_repo=None, jobs_repo=jobs)
        logger.run_start(_start_event())
        await asyncio.sleep(0.05)
        assert len(jobs.calls) == 1
        row = jobs.calls[0]
        assert row["run_id"] == "evt-1"
        assert row["org_id"] == "org-1"
        assert row["name"] == "github"
        assert row["job_type"] == "agent"
        assert row["schedule"] == "adhoc"
        assert row["configuration"] == {}
        assert row["status"] == "running"

    asyncio.run(_run())
