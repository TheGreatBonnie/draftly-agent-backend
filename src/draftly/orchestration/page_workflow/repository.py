"""Persistence for the durable page-scoped documentation workflow.

Mirrors ``drafts.py``: one ``DatabaseClient``, asyncpg-style calls. Versions
are allocated atomically through ``documentation_page_states.next_version`` so
two writers can never reserve the same version. Late completions cannot
replace a newer artifact: ``record_artifact`` only promotes when the incoming
version is strictly newer than the page's ``latest_version``. Tasks are
claimed durably (``FOR UPDATE SKIP LOCKED`` + lease) so two workers never run
the same task, and an infrastructure failure retries once before failing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from draftly.integrations.database.client import DatabaseClient
from draftly.orchestration.page_workflow.models import PageEvaluationResult

MAX_AUTOMATIC_EVALUATION_ATTEMPTS = 3


class StaleArtifactError(Exception):  # noqa: N818 - public API name from the plan
    """An artifact write no longer targets the page's current artifact.

    Raised when recording an artifact that is older than the page's promoted
    latest version, or when evaluating an artifact that is no longer current.
    """


@dataclass(frozen=True)
class NewPage:
    """One page row to seed into ``documentation_page_states``."""

    page_id: str
    path: str
    action: str


@dataclass(frozen=True)
class PageState:
    """Read model of one ``documentation_page_states`` row."""

    run_id: str
    org_id: str
    page_id: str
    path: str
    action: str
    status: str
    latest_artifact_id: str | None
    latest_version: int
    next_version: int
    evaluation_attempt: int
    escalation_reason: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class WorkflowTask:
    """Read model of one ``documentation_workflow_tasks`` row."""

    run_id: str
    task_id: str
    org_id: str
    task_type: str
    page_id: str | None
    artifact_version: int | None
    dependencies: list[str]
    status: str
    infrastructure_retries: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    input_data: dict[str, Any]
    output_data: dict[str, Any] | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class RevisionSchedule:
    """Atomic write/evaluate pair derived from one accepted source artifact."""

    page_id: str
    source_artifact_id: str
    source_version: int
    attempt: int
    write_input: dict[str, Any]
    evaluate_input: dict[str, Any]


class PageWorkflowRepository:
    """Persistence for page states, evaluations, and workflow tasks.

    Writes are conditional so a stale writer cannot clobber newer work, and
    task claims are atomic with lease assignment (``SKIP LOCKED``) so parallel
    workers never double-run a task.
    """

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    # -- page states ---------------------------------------------------------

    async def create_pages(
        self,
        *,
        run_id: str,
        org_id: str,
        pages: Iterable[NewPage],
    ) -> None:
        """Seed page workflow states; existing rows are left untouched."""
        for page in pages:
            await self.database.execute(
                """
                INSERT INTO documentation_page_states (
                    run_id, org_id, page_id, path, action
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (run_id, page_id) DO NOTHING
                """,
                run_id,
                org_id,
                page.page_id,
                page.path,
                page.action,
            )

    async def reserve_next_version(self, *, run_id: str, page_id: str) -> int:
        """Atomically allocate the next artifact version for one page.

        The single-statement UPDATE locks the page-state row while it bumps
        ``next_version``, so concurrent reservations never repeat a version.
        """
        row = await self.database.fetch_one(
            """
            UPDATE documentation_page_states
               SET next_version = next_version + 1
             WHERE run_id = $1 AND page_id = $2
             RETURNING next_version - 1 AS version
            """,
            run_id,
            page_id,
        )
        if row is None:
            raise ValueError(f"page state not found for page_id {page_id!r}")
        return int(row["version"])

    async def record_artifact(
        self,
        *,
        run_id: str,
        page_id: str,
        artifact_id: str,
        version: int,
    ) -> None:
        """Promote an artifact to the page's latest, conditionally.

        Only promotes when ``version`` is strictly newer than the page's
        current ``latest_version``; exactly one row must update or the write
        is stale and ``StaleArtifactError`` is raised.
        """
        row = await self.database.fetch_one(
            """
            WITH promoted AS (
                UPDATE documentation_page_states
                   SET latest_artifact_id = $3,
                       latest_version = $4,
                       status = 'evaluating',
                       updated_at = now()
                 WHERE run_id = $1
                   AND page_id = $2
                   AND (
                       latest_version < $4
                       OR (
                           latest_version = $4
                           AND latest_artifact_id = $3
                       )
                   )
                 RETURNING latest_artifact_id
            )
            SELECT count(*) AS updated FROM promoted
            """,
            run_id,
            page_id,
            artifact_id,
            version,
        )
        updated = int(row["updated"]) if row else 0
        if updated != 1:
            raise StaleArtifactError(
                f"artifact {artifact_id!r} v{version} is not the page's next artifact"
            )

    async def get_page_states(self, *, run_id: str) -> list[PageState]:
        rows = await self.database.fetch_all(
            """
            SELECT * FROM documentation_page_states
             WHERE run_id = $1
             ORDER BY page_id
            """,
            run_id,
        )
        return [self._to_state(row) for row in rows]

    # -- evaluations ---------------------------------------------------------

    async def record_evaluation(
        self,
        result: PageEvaluationResult,
        *,
        run_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        """Persist one immutable evaluation, verifying identity first.

        The artifact must exist and be sealed; its run/org are derived from
        the artifact row unless passed explicitly. The page state must still
        point at that artifact (ID, version, and hash must match) or the
        evaluation is stale. All reads and the insert share one transaction.
        """
        async with self.database.transaction() as conn:
            artifact = await conn.fetchrow(
                """
                SELECT run_id, org_id, version, content_hash, sealed
                  FROM draft_revisions
                 WHERE id = $1
                """,
                result.artifact_id,
            )
            if artifact is None or not bool(artifact["sealed"]):
                raise StaleArtifactError(
                    f"artifact {result.artifact_id!r} is not a sealed revision"
                )
            run = run_id or str(artifact["run_id"])
            org = org_id or str(artifact["org_id"])
            page = await conn.fetchrow(
                """
                SELECT latest_artifact_id, latest_version
                  FROM documentation_page_states
                 WHERE run_id = $1 AND page_id = $2
                 FOR UPDATE
                """,
                run,
                result.page_id,
            )
            if page is None:
                raise StaleArtifactError(f"page {result.page_id!r} has no workflow state")
            if str(page["latest_artifact_id"]) != result.artifact_id:
                raise StaleArtifactError(
                    "evaluation targets an artifact that is not the page's current artifact"
                )
            if int(page["latest_version"]) != result.version:
                raise StaleArtifactError("evaluation targets a stale artifact version")
            if str(artifact["content_hash"]) != result.content_hash:
                raise StaleArtifactError("evaluation content hash does not match the artifact")
            persisted = await conn.fetchrow(
                """
                INSERT INTO documentation_page_evaluations (
                    run_id, org_id, page_id, artifact_id, version, content_hash,
                    attempt, status, score, metrics, revision_feedback
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (run_id, artifact_id) DO UPDATE
                    SET artifact_id = EXCLUDED.artifact_id
                RETURNING status, attempt, revision_feedback
                """,
                run,
                org,
                result.page_id,
                result.artifact_id,
                result.version,
                result.content_hash,
                result.attempt,
                result.status,
                result.score,
                [metric.model_dump() for metric in result.metrics],
                list(result.revision_feedback),
            )
            persisted_status = str(persisted["status"]) if persisted else result.status
            persisted_attempt = int(persisted["attempt"]) if persisted else result.attempt
            persisted_feedback = (
                list(persisted["revision_feedback"])
                if persisted
                else list(result.revision_feedback)
            )
            page_status = {
                "passed": "passed",
                "revision_required": "revising",
                "awaiting_human_review": "awaiting_human_review",
            }[persisted_status]
            escalation_reason = (
                "; ".join(str(item) for item in persisted_feedback)
                if persisted_status == "awaiting_human_review"
                else None
            )
            projected = await conn.fetchrow(
                """
                UPDATE documentation_page_states
                   SET status = $6,
                       evaluation_attempt = $7,
                       escalation_reason = $8,
                       updated_at = now()
                 WHERE run_id = $1
                   AND page_id = $2
                   AND latest_artifact_id = $3
                   AND latest_version = $4
                   AND EXISTS (
                       SELECT 1 FROM draft_revisions
                        WHERE id = $3 AND content_hash = $5 AND sealed = TRUE
                   )
                 RETURNING page_id
                """,
                run,
                result.page_id,
                result.artifact_id,
                result.version,
                result.content_hash,
                page_status,
                persisted_attempt,
                escalation_reason,
            )
            if projected is None:
                raise StaleArtifactError(
                    "evaluation state projection lost the current artifact identity"
                )

    async def schedule_revisions(
        self,
        *,
        run_id: str,
        org_id: str,
        revisions: list[RevisionSchedule],
    ) -> list[int]:
        """Atomically create idempotent write/evaluate pairs for page revisions."""
        if not revisions:
            return []
        page_ids = [revision.page_id for revision in revisions]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("revision batch contains duplicate page IDs")
        if any(
            revision.attempt < 1
            or revision.attempt > MAX_AUTOMATIC_EVALUATION_ATTEMPTS
            for revision in revisions
        ):
            raise ValueError("automatic page evaluation attempt must be between 1 and 3")

        versions: list[int] = []
        async with self.database.transaction() as conn:
            for revision in revisions:
                page = await conn.fetchrow(
                    """
                    SELECT latest_artifact_id, latest_version, evaluation_attempt
                      FROM documentation_page_states
                     WHERE run_id = $1 AND page_id = $2
                     FOR UPDATE
                    """,
                    run_id,
                    revision.page_id,
                )
                if page is None:
                    raise StaleArtifactError(
                        f"page {revision.page_id!r} has no workflow state"
                    )
                if (
                    str(page["latest_artifact_id"]) != revision.source_artifact_id
                    or int(page["latest_version"]) != revision.source_version
                ):
                    raise StaleArtifactError(
                        f"revision for {revision.page_id!r} targets a stale artifact"
                    )
                if int(page["evaluation_attempt"]) + 1 != revision.attempt:
                    raise ValueError(
                        f"revision attempt {revision.attempt} is not the next page attempt"
                    )

                next_version = revision.source_version + 1
                write_id = f"write:{revision.page_id}:{next_version}"
                evaluate_id = f"evaluate:{revision.page_id}:{next_version}"
                await conn.execute(
                    """
                    UPDATE documentation_page_states
                       SET next_version = GREATEST(next_version, $3 + 1),
                           status = 'revising',
                           updated_at = now()
                     WHERE run_id = $1 AND page_id = $2
                    """,
                    run_id,
                    revision.page_id,
                    next_version,
                )
                await conn.execute(
                    """
                    INSERT INTO documentation_workflow_tasks (
                        run_id, task_id, org_id, task_type, page_id,
                        artifact_version, dependencies, input_data, status
                    ) VALUES ($1, $2, $3, 'write', $4, $5, '[]'::jsonb, $6, 'pending')
                    ON CONFLICT (run_id, task_id) DO NOTHING
                    """,
                    run_id,
                    write_id,
                    org_id,
                    revision.page_id,
                    next_version,
                    dict(revision.write_input),
                )
                await conn.execute(
                    """
                    INSERT INTO documentation_workflow_tasks (
                        run_id, task_id, org_id, task_type, page_id,
                        artifact_version, dependencies, input_data, status
                    ) VALUES ($1, $2, $3, 'evaluate', $4, $5, $6, $7, 'pending')
                    ON CONFLICT (run_id, task_id) DO NOTHING
                    """,
                    run_id,
                    evaluate_id,
                    org_id,
                    revision.page_id,
                    next_version,
                    [write_id],
                    dict(revision.evaluate_input),
                )
                versions.append(next_version)
        return versions

    # -- workflow tasks ------------------------------------------------------

    async def enqueue_task(
        self,
        *,
        run_id: str,
        task_id: str,
        org_id: str,
        task_type: str,
        page_id: str | None = None,
        artifact_version: int | None = None,
        dependencies: list[str] | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> None:
        """Insert a pending task; re-enqueueing the same (run, task) is a no-op."""
        await self.database.execute(
            """
            INSERT INTO documentation_workflow_tasks (
                run_id, task_id, org_id, task_type, page_id, artifact_version,
                dependencies, input_data, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
            ON CONFLICT (run_id, task_id) DO NOTHING
            """,
            run_id,
            task_id,
            org_id,
            task_type,
            page_id,
            artifact_version,
            list(dependencies or []),
            dict(input_data or {}),
        )

    async def claim_ready_tasks(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_seconds: int = 300,
        limit: int = 10,
    ) -> list[WorkflowTask]:
        """Atomically claim pending tasks whose dependencies all completed.

        The lock-and-update is one statement: each claimed row is set to
        ``running`` with a fresh lease, and ``FOR UPDATE SKIP LOCKED`` keeps
        concurrent workers from double-claiming.
        """
        rows = await self.database.fetch_all(
            """
            UPDATE documentation_workflow_tasks t
               SET status = 'running',
                   lease_owner = $3,
                   lease_expires_at = now() + make_interval(secs => $4),
                   updated_at = now()
             WHERE t.run_id = $1
               AND t.task_id IN (
                   SELECT inner_t.task_id
                     FROM documentation_workflow_tasks inner_t
                    WHERE inner_t.run_id = $1
                      AND inner_t.status = 'pending'
                      AND (
                          inner_t.dependencies = '[]'::jsonb
                          OR NOT EXISTS (
                              SELECT 1
                                FROM jsonb_array_elements_text(inner_t.dependencies)
                                     AS dep(task_id)
                               WHERE NOT EXISTS (
                                   SELECT 1
                                     FROM documentation_workflow_tasks c
                                    WHERE c.run_id = inner_t.run_id
                                      AND c.task_id = dep.task_id
                                      AND c.status = 'completed'
                               )
                          )
                      )
                  ORDER BY inner_t.created_at, inner_t.task_id
                  FOR UPDATE SKIP LOCKED
                  LIMIT $2
               )
             RETURNING t.*
            """,
            run_id,
            limit,
            lease_owner,
            lease_seconds,
        )
        return [self._to_task(row) for row in rows]

    async def complete_task(
        self,
        *,
        run_id: str,
        task_id: str,
        owner: str,
        output_data: dict[str, Any] | None = None,
    ) -> bool:
        """Complete a task whose lease ``owner`` currently holds.

        Returns False when the task is not running. When the task is running
        under a different lease owner, the completion is stale and
        ``StaleArtifactError`` is raised.
        """
        row = await self.database.fetch_one(
            """
            WITH done AS (
                UPDATE documentation_workflow_tasks
                   SET status = 'completed',
                       output_data = $4,
                       error = NULL,
                       updated_at = now()
                 WHERE run_id = $1
                   AND task_id = $2
                   AND status = 'running'
                   AND lease_owner = $3
                 RETURNING task_id
            )
            SELECT
                (SELECT count(*) FROM done) AS updated,
                EXISTS (
                    SELECT 1
                      FROM documentation_workflow_tasks
                     WHERE run_id = $1
                       AND task_id = $2
                       AND status = 'running'
                       AND lease_owner IS DISTINCT FROM $3
                ) AS owned_by_other
            """,
            run_id,
            task_id,
            owner,
            output_data,
        )
        updated = int(row["updated"]) if row else 0
        owned_by_other = bool(row["owned_by_other"]) if row else False
        if owned_by_other and updated == 0:
            raise StaleArtifactError(
                f"task {task_id!r} is leased by another worker, not {owner!r}"
            )
        return updated > 0

    async def retry_or_fail_task(
        self,
        *,
        run_id: str,
        task_id: str,
        owner: str,
        error: str,
    ) -> str | None:
        """Recycle the first infrastructure failure, fail the second.

        Returns the resulting status (``"pending"`` after the first failure,
        ``"failed"`` after the second) or ``None`` when no task is running.
        When the running task is owned by a different lease owner,
        ``StaleArtifactError`` is raised.
        """
        row = await self.database.fetch_one(
            """
            WITH retried AS (
                UPDATE documentation_workflow_tasks
                   SET status = CASE
                                    WHEN infrastructure_retries < 1 THEN 'pending'
                                    ELSE 'failed'
                                END,
                       infrastructure_retries = LEAST(infrastructure_retries + 1, 1),
                       error = $4,
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       updated_at = now()
                 WHERE run_id = $1
                   AND task_id = $2
                   AND status = 'running'
                   AND lease_owner = $3
                 RETURNING status
            )
            SELECT
                (SELECT status FROM retried) AS status,
                EXISTS (
                    SELECT 1
                      FROM documentation_workflow_tasks
                     WHERE run_id = $1
                       AND task_id = $2
                       AND status = 'running'
                       AND lease_owner IS DISTINCT FROM $3
                ) AS owned_by_other
            """,
            run_id,
            task_id,
            owner,
            error,
        )
        updated = row["status"] if row else None
        owned_by_other = bool(row["owned_by_other"]) if row else False
        if owned_by_other and not updated:
            raise StaleArtifactError(
                f"task {task_id!r} is leased by another worker, not {owner!r}"
            )
        if not updated:
            return None
        return str(updated)

    async def reset_expired_leases(self, *, run_id: str) -> int:
        """Return running tasks whose leases expired back to pending.

        Ownership is cleared so the next ``claim_ready_tasks`` can re-claim
        them. Infrastructure retries are untouched: lease expiry is not an
        execution failure.
        """
        row = await self.database.fetch_one(
            """
            WITH recycled AS (
                UPDATE documentation_workflow_tasks
                   SET status = 'pending',
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       updated_at = now()
                 WHERE run_id = $1
                   AND status = 'running'
                   AND lease_expires_at < now()
                 RETURNING task_id
            )
            SELECT count(*) AS updated FROM recycled
            """,
            run_id,
        )
        return int(row["updated"]) if row else 0

    async def get_tasks(self, *, run_id: str) -> list[WorkflowTask]:
        """Return every workflow task of a run in enqueue order.

        Read-only snapshot the executor consults when nothing is claimable to
        distinguish a working in-flight batch (running tasks still on lease)
        from a true deadlock (pending tasks that can never claim).
        """
        rows = await self.database.fetch_all(
            """
            SELECT * FROM documentation_workflow_tasks
             WHERE run_id = $1
             ORDER BY created_at, task_id
            """,
            run_id,
        )
        return [self._to_task(row) for row in rows]

    # -- row mapping ---------------------------------------------------------

    @staticmethod
    def _to_state(row: Any) -> PageState:
        latest_artifact_id = row.get("latest_artifact_id")
        escalation_reason = row.get("escalation_reason")
        return PageState(
            run_id=str(row["run_id"]),
            org_id=str(row["org_id"]),
            page_id=str(row["page_id"]),
            path=str(row["path"]),
            action=str(row["action"]),
            status=str(row["status"]),
            latest_artifact_id=(
                str(latest_artifact_id) if latest_artifact_id is not None else None
            ),
            latest_version=int(row["latest_version"] or 0),
            next_version=int(row["next_version"] or 1),
            evaluation_attempt=int(row["evaluation_attempt"] or 0),
            escalation_reason=(
                str(escalation_reason) if escalation_reason is not None else None
            ),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _to_task(row: Any) -> WorkflowTask:
        page_id = row.get("page_id")
        artifact_version = row.get("artifact_version")
        lease_owner = row.get("lease_owner")
        output_data = row.get("output_data")
        return WorkflowTask(
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            org_id=str(row["org_id"]),
            task_type=str(row["task_type"]),
            page_id=str(page_id) if page_id is not None else None,
            artifact_version=int(artifact_version) if artifact_version is not None else None,
            dependencies=list(row.get("dependencies") or []),
            status=str(row["status"]),
            infrastructure_retries=int(row.get("infrastructure_retries") or 0),
            lease_owner=str(lease_owner) if lease_owner is not None else None,
            lease_expires_at=row.get("lease_expires_at"),
            input_data=dict(row.get("input_data") or {}),
            output_data=output_data,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
