"""PageWorkflowRepository contract tests.

Covers concurrent version allocation, SHA-256 finalization, newest-per-path
lookup, stale artifact rejection, atomic ready-task claims, dependency gating,
first/second infrastructure failure semantics, and expired lease recovery.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draftly.orchestration.page_workflow.models import MetricResult, PageEvaluationResult
from draftly.orchestration.page_workflow.repository import (
    NewPage,
    PageWorkflowRepository,
    StaleArtifactError,
)
from draftly.persistence.repositories.drafts import DraftRepository

AUCTION_EPOCH = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _evaluation(
    artifact_id: str,
    version: int,
    *,
    status: str = "passed",
    page_id: str = "docs/a.md",
    content_hash: str | None = None,
) -> PageEvaluationResult:
    return PageEvaluationResult(
        page_id=page_id,
        artifact_id=artifact_id,
        version=version,
        content_hash=content_hash or hashlib.sha256(b"alpha").hexdigest(),
        attempt=1,
        status=status,
        score=0.9,
        metrics=[
            MetricResult(
                name="quality",
                score=0.9,
                threshold=0.7,
                passed=True,
                blocking=True,
                reason="ok",
            )
        ],
        revision_feedback=[],
    )


class FakeClient:
    """In-memory fake mirroring the SQL the repositories issue.

    Doubles as the ``asyncpg.Connection`` the workflows layer uses inside
    ``DatabaseClient.transaction``: ``transaction()`` yields ``self`` and the
    connection-level ``fetchrow``/``fetch``/``execute`` route to the same
    dispatch as the client-level surface.
    """

    def __init__(self) -> None:
        self.revisions: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.page_states: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []
        self.now = AUCTION_EPOCH

    # -- DatabaseClient surface (module-level helper above calls these) ------

    @asynccontextmanager
    async def transaction(self, *, isolation: str = "read_committed") -> Any:
        yield self

    async def execute(self, query: str, *args: Any) -> str:
        await self._dispatch(query, *args)
        return "OK"

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return await self._dispatch(query, *args)

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        rows = await self._dispatch(query, *args)
        return rows[0] if rows else None

    # -- asyncpg.Connection surface (used inside transactions) ---------------

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        rows = await self._dispatch(query, *args)
        return rows[0] if rows else None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return await self._dispatch(query, *args)

    # -- dispatch --------------------------------------------------------------

    async def _dispatch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.strip()
        up = q.upper()

        if up.startswith("SELECT MAX(GENERATION)"):
            rows = [r for r in self.revisions if r["run_id"] == args[0]]
            return [{"generation": max(r["generation"] for r in rows)}] if rows else []

        if up.startswith("INSERT INTO DRAFT_REVISIONS"):
            self.revisions.append(
                {
                    "id": args[0],
                    "run_id": args[1],
                    "org_id": args[2],
                    "generation": args[3],
                    "path": args[4],
                    "action": args[5],
                    "version": args[6],
                    "content_hash": args[7],
                    "sealed": False,
                    "content_size": 0,
                    "created_at": args[8],
                    "sealed_at": None,
                }
            )
            return []

        if up.startswith("INSERT INTO DRAFT_CHUNKS"):
            self.chunks.append(
                {
                    "draft_id": args[0],
                    "chunk_index": args[1],
                    "content": args[2],
                    "created_at": "2026-09-20T12:00:00Z",
                }
            )
            return []

        if up.startswith("UPDATE DRAFT_REVISIONS"):
            draft_id = args[0]
            for row in self.revisions:
                if row["id"] == draft_id and "SEALED = TRUE" in up and "CONTENT_SIZE" in up:
                    row["sealed"] = True
                    row["content_size"] = args[1]
                    if "CONTENT_HASH" in up:
                        row["content_hash"] = args[2]
            return []

        if "FROM DRAFT_CHUNKS" in up:
            draft_id = args[0]
            rows = [c for c in self.chunks if c["draft_id"] == draft_id]
            return sorted(rows, key=lambda c: c["chunk_index"])

        if "FROM DRAFT_REVISIONS" in up and "WHERE ID = $1" in up:
            return [r for r in self.revisions if r["id"] == args[0]]

        if up.startswith("INSERT INTO DOCUMENTATION_PAGE_STATES"):
            run_id, org_id, page_id, path, action = args
            if not any(
                s["run_id"] == run_id and s["page_id"] == page_id for s in self.page_states
            ):
                self.page_states.append(
                    {
                        "run_id": run_id,
                        "org_id": org_id,
                        "page_id": page_id,
                        "path": path,
                        "action": action,
                        "status": "pending",
                        "latest_artifact_id": None,
                        "latest_version": 0,
                        "next_version": 1,
                        "evaluation_attempt": 0,
                        "escalation_reason": None,
                        "updated_at": self.now,
                    }
                )
            return []

        if "WITH PROMOTED AS" in up:
            run_id, page_id, artifact_id, version = args
            state = next(
                (s for s in self.page_states if s["run_id"] == run_id and s["page_id"] == page_id),
                None,
            )
            if state is not None and state["latest_version"] < version:
                state["latest_artifact_id"] = artifact_id
                state["latest_version"] = version
                state["status"] = "evaluating"
                state["updated_at"] = self.now
                return [{"updated": 1}]
            return [{"updated": 0}]

        if "RETURNING NEXT_VERSION - 1 AS VERSION" in up:
            run_id, page_id = args
            state = next(
                (s for s in self.page_states if s["run_id"] == run_id and s["page_id"] == page_id),
                None,
            )
            if state is None:
                return []
            version = state["next_version"]
            state["next_version"] = version + 1
            return [{"version": version}]

        if "FROM DOCUMENTATION_PAGE_STATES" in up:
            run_id = args[0]
            states = [s for s in self.page_states if s["run_id"] == run_id]
            if len(args) > 1 and args[1] is not None:
                states = [s for s in states if s["page_id"] == args[1]]
            return sorted(states, key=lambda s: s["page_id"])

        if up.startswith("INSERT INTO DOCUMENTATION_WORKFLOW_TASKS"):
            (
                run_id,
                task_id,
                org_id,
                task_type,
                page_id,
                artifact_version,
                dependencies,
                input_data,
            ) = args
            if not any(t["run_id"] == run_id and t["task_id"] == task_id for t in self.tasks):
                self.tasks.append(
                    {
                        "run_id": run_id,
                        "task_id": task_id,
                        "org_id": org_id,
                        "task_type": task_type,
                        "page_id": page_id,
                        "artifact_version": artifact_version,
                        "dependencies": dependencies,
                        "status": "pending",
                        "infrastructure_retries": 0,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "input_data": input_data,
                        "output_data": None,
                        "error": None,
                        "created_at": self.now,
                        "updated_at": self.now,
                    }
                )
            return []

        if up.startswith("SELECT * FROM DOCUMENTATION_WORKFLOW_TASKS"):
            run_id = args[0]
            tasks = [t for t in self.tasks if t["run_id"] == run_id]
            return sorted(tasks, key=lambda t: (t["created_at"], t["task_id"]))

        if "FOR UPDATE SKIP LOCKED" in up:
            run_id, limit, lease_owner, lease_seconds = args
            completed = {
                t["task_id"]
                for t in self.tasks
                if t["run_id"] == run_id and t["status"] == "completed"
            }

            def ready(task: dict[str, Any]) -> bool:
                return task["status"] == "pending" and all(
                    dep in completed for dep in (task["dependencies"] or [])
                )

            claimed = [t for t in self.tasks if t["run_id"] == run_id and ready(t)]
            claimed.sort(key=lambda t: (t["created_at"], t["task_id"]))
            claimed = claimed[:limit]
            for task in claimed:
                task["status"] = "running"
                task["lease_owner"] = lease_owner
                task["lease_expires_at"] = self.now + timedelta(seconds=lease_seconds)
                task["updated_at"] = self.now
            return claimed

        if "WITH RETRIED AS" in up:
            run_id, task_id, owner, error = args
            task = next(
                (
                    t
                    for t in self.tasks
                    if (
                        t["run_id"] == run_id
                        and t["task_id"] == task_id
                        and t["status"] == "running"
                    )
                ),
                None,
            )
            if task is None:
                return [{"status": None, "owned_by_other": False}]
            if task["lease_owner"] != owner:
                return [{"status": None, "owned_by_other": True}]
            if task["infrastructure_retries"] < 1:
                task["status"] = "pending"
            else:
                task["status"] = "failed"
            task["infrastructure_retries"] = min(task["infrastructure_retries"] + 1, 1)
            task["error"] = error
            task["lease_owner"] = None
            task["lease_expires_at"] = None
            task["updated_at"] = self.now
            return [{"status": task["status"], "owned_by_other": False}]

        if "WITH DONE AS" in up:
            run_id, task_id, owner, output_data = args
            task = next(
                (
                    t
                    for t in self.tasks
                    if (
                        t["run_id"] == run_id
                        and t["task_id"] == task_id
                        and t["status"] == "running"
                    )
                ),
                None,
            )
            if task is None:
                return [{"updated": 0, "owned_by_other": False}]
            if task["lease_owner"] != owner:
                return [{"updated": 0, "owned_by_other": True}]
            task["status"] = "completed"
            task["output_data"] = output_data
            task["error"] = None
            task["updated_at"] = self.now
            return [{"updated": 1, "owned_by_other": False}]

        if "LEASE_EXPIRES_AT < NOW()" in up:
            run_id = args[0]
            recycled = 0
            for task in self.tasks:
                if (
                    task["run_id"] == run_id
                    and task["status"] == "running"
                    and task["lease_expires_at"] is not None
                    and task["lease_expires_at"] < self.now
                ):
                    task["status"] = "pending"
                    task["lease_owner"] = None
                    task["lease_expires_at"] = None
                    task["updated_at"] = self.now
                    recycled += 1
            return [{"updated": recycled}]

        if up.startswith("INSERT INTO DOCUMENTATION_PAGE_EVALUATIONS"):
            self.evaluations.append(
                {
                    "run_id": args[0],
                    "org_id": args[1],
                    "page_id": args[2],
                    "artifact_id": args[3],
                    "version": args[4],
                    "content_hash": args[5],
                    "attempt": args[6],
                    "status": args[7],
                    "score": args[8],
                    "metrics": args[9],
                    "revision_feedback": args[10],
                    "created_at": self.now,
                }
            )
            return []

        raise AssertionError(f"fake does not understand query: {q}")

    # -- helpers used by tests ------------------------------------------------

    def claim_for(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tasks if t["task_id"] == task_id), None)


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def pages(client: FakeClient) -> PageWorkflowRepository:
    return PageWorkflowRepository(database=client)


@pytest.fixture
def drafts(client: FakeClient) -> DraftRepository:
    return DraftRepository(database=client)


async def _seed_page(pages: PageWorkflowRepository) -> None:
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[NewPage(page_id="docs/a.md", path="docs/a.md", action="update")],
    )


async def _seed_artifact(
    client: FakeClient,
    drafts: DraftRepository,
    pages: PageWorkflowRepository,
    generation: int,
    version: int,
    content: str = "alpha",
) -> Any:
    revision = await drafts.create_revision(
        run_id="run-1",
        org_id="org-1",
        generation=generation,
        path="docs/a.md",
        action="update",
        version=version,
    )
    await drafts.append_chunk(revision.id, content)
    return await drafts.finalize(revision.id)


async def test_reserve_next_version_allocates_monotonically_per_page(
    pages: PageWorkflowRepository,
) -> None:
    await _seed_page(pages)
    first = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    second = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    assert (first, second) == (1, 2)
    third = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    assert third == 3


async def test_reserve_next_version_is_independent_across_pages(
    pages: PageWorkflowRepository,
) -> None:
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[
            NewPage(page_id="docs/a.md", path="docs/a.md", action="update"),
            NewPage(page_id="docs/b.md", path="docs/b.md", action="create"),
        ],
    )
    a = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    b = await pages.reserve_next_version(run_id="run-1", page_id="docs/b.md")
    assert (a, b) == (1, 1)


async def test_reserve_next_version_unknown_page_raises(
    pages: PageWorkflowRepository,
) -> None:
    with pytest.raises(ValueError, match="page state"):
        await pages.reserve_next_version(run_id="run-1", page_id="docs/missing.md")


async def test_versioned_artifact_seals_with_sha256(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    assert sealed.version == version
    assert sealed.content_hash == hashlib.sha256(b"alpha").hexdigest()


async def test_artifact_identity_brief_sequence(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    first_version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    second_version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    first = await drafts.create_revision(
        run_id="run-1",
        org_id="org-1",
        generation=1,
        path="docs/a.md",
        action="update",
        version=first_version,
    )
    second = await drafts.create_revision(
        run_id="run-1",
        org_id="org-1",
        generation=2,
        path="docs/a.md",
        action="update",
        version=second_version,
    )
    assert (first.version, second.version) == (1, 2)

    await drafts.append_chunk(first.id, "alpha")
    sealed = await drafts.finalize(first.id)
    assert sealed.content_hash == hashlib.sha256(b"alpha").hexdigest()


async def test_record_artifact_promotes_page_state(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    states = await pages.get_page_states(run_id="run-1")
    assert len(states) == 1
    state = states[0]
    assert state.latest_artifact_id == sealed.id
    assert state.latest_version == 1
    assert state.status == "evaluating"
    assert state.next_version == 2


async def test_record_artifact_rejects_replaying_same_or_older_version(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    with pytest.raises(StaleArtifactError):
        await pages.record_artifact(
            run_id="run-1",
            page_id="docs/a.md",
            artifact_id=sealed.id,
            version=version,
        )


async def test_record_evaluation_accepts_current_artifact(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    result = _evaluation(sealed.id, version)
    await pages.record_evaluation(result)
    assert len(client.evaluations) == 1
    assert client.evaluations[0]["artifact_id"] == sealed.id


async def test_record_evaluation_rejects_stale_artifact(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    first_version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    first = await _seed_artifact(
        client, drafts, pages, generation=1, version=first_version
    )
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=first.id,
        version=first_version,
    )
    await pages.record_evaluation(_evaluation(first.id, first_version))

    second_version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    second = await _seed_artifact(
        client, drafts, pages, generation=2, version=second_version, content="beta"
    )
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=second.id,
        version=second_version,
    )

    stale_result = _evaluation(first.id, first_version)
    with pytest.raises(StaleArtifactError):
        await pages.record_evaluation(stale_result)
    assert len(client.evaluations) == 1


async def test_record_evaluation_rejects_version_mismatch_of_current_artifact(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    mismatch = _evaluation(sealed.id, version + 1)
    with pytest.raises(StaleArtifactError):
        await pages.record_evaluation(mismatch)


async def test_record_evaluation_rejects_content_hash_mismatch(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    tampered = _evaluation(
        sealed.id, version, content_hash=hashlib.sha256(b"tampered").hexdigest()
    )
    with pytest.raises(StaleArtifactError):
        await pages.record_evaluation(tampered)
    assert len(client.evaluations) == 0


async def test_record_evaluation_applies_page_filter_in_multipage_run(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[
            NewPage(page_id="docs/a.md", path="docs/a.md", action="update"),
            NewPage(page_id="docs/b.md", path="docs/b.md", action="create"),
        ],
    )
    b_version = await pages.reserve_next_version(run_id="run-1", page_id="docs/b.md")
    b_revision = await drafts.create_revision(
        run_id="run-1",
        org_id="org-1",
        generation=1,
        path="docs/b.md",
        action="create",
        version=b_version,
    )
    await drafts.append_chunk(b_revision.id, "beta")
    sealed_b = await drafts.finalize(b_revision.id)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/b.md",
        artifact_id=sealed_b.id,
        version=b_version,
    )
    result = _evaluation(
        sealed_b.id,
        b_version,
        page_id="docs/b.md",
        content_hash=hashlib.sha256(b"beta").hexdigest(),
    )
    await pages.record_evaluation(result)
    assert len(client.evaluations) == 1
    assert client.evaluations[0]["page_id"] == "docs/b.md"


async def test_record_evaluation_rejects_unknown_artifact(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await _seed_page(pages)
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    with pytest.raises(StaleArtifactError):
        await pages.record_evaluation(_evaluation("draft-missing", version))


async def test_claim_ready_tasks_claims_only_runnable(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    await client.execute(
        """INSERT INTO documentation_workflow_tasks
             (run_id, task_id, org_id, task_type, page_id, artifact_version,
              dependencies, input_data, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
        "run-1",
        "write-a",
        "org-1",
        "write",
        "docs/a.md",
        1,
        [],
        {},
    )
    claimed = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert len(claimed) == 1
    task = claimed[0]
    assert task.task_id == "write-a"
    assert task.status == "running"
    assert task.lease_owner == "worker-1"
    assert task.lease_expires_at is not None


async def test_claim_ready_tasks_respects_dependencies(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    async def enqueue(task_id: str, *deps: str) -> None:
        await client.execute(
            """INSERT INTO documentation_workflow_tasks
                 (run_id, task_id, org_id, task_type, page_id, artifact_version,
                  dependencies, input_data, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
            "run-1",
            task_id,
            "org-1",
            "write",
            "docs/a.md",
            1,
            list(deps),
            {},
        )

    await enqueue("write-a")
    await enqueue("write-b", "write-a")
    await enqueue("evaluate-a", "write-a", "write-b")

    first = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert [t.task_id for t in first] == ["write-a"]
    assert await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1") == []

    await pages.complete_task(
        run_id="run-1", task_id="write-a", output_data={"ok": True}, owner="worker-1"
    )
    second = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert [t.task_id for t in second] == ["write-b"]

    await pages.complete_task(
        run_id="run-1", task_id="write-b", output_data={"ok": True}, owner="worker-1"
    )
    third = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert [t.task_id for t in third] == ["evaluate-a"]


async def test_complete_task_requires_running_lease(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    async def enqueue(task_id: str) -> None:
        await client.execute(
            """INSERT INTO documentation_workflow_tasks
                 (run_id, task_id, org_id, task_type, page_id, artifact_version,
                  dependencies, input_data, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
            "run-1",
            task_id,
            "org-1",
            "write",
            "docs/a.md",
            1,
            [],
            {},
        )

    await enqueue("write-a")
    assert (
        await pages.complete_task(
            run_id="run-1", task_id="write-a", output_data=None, owner="worker-1"
        )
        is False
    )
    await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert (
        await pages.complete_task(
            run_id="run-1",
            task_id="write-a",
            output_data={"ok": True},
            owner="worker-1",
        )
        is True
    )


async def test_retry_or_fail_task_retries_first_infra_failure_then_fails(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    async def enqueue(task_id: str) -> None:
        await client.execute(
            """INSERT INTO documentation_workflow_tasks
                 (run_id, task_id, org_id, task_type, page_id, artifact_version,
                  dependencies, input_data, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
            "run-1",
            task_id,
            "org-1",
            "write",
            "docs/a.md",
            1,
            [],
            {},
        )

    await enqueue("write-a")
    await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")

    returned = await pages.retry_or_fail_task(
        run_id="run-1", task_id="write-a", owner="worker-1", error="infra boom"
    )
    assert returned == "pending"
    claimed = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")
    assert [t.task_id for t in claimed] == ["write-a"]

    returned = await pages.retry_or_fail_task(
        run_id="run-1", task_id="write-a", owner="worker-1", error="infra boom again"
    )
    assert returned == "failed"
    row = client.claim_for("write-a")
    assert row["status"] == "failed"
    assert row["infrastructure_retries"] == 1
    assert await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1") == []


async def test_stale_owner_cannot_complete_or_retry_recycled_task(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    await client.execute(
        """INSERT INTO documentation_workflow_tasks
             (run_id, task_id, org_id, task_type, page_id, artifact_version,
              dependencies, input_data, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
        "run-1",
        "write-a",
        "org-1",
        "write",
        "docs/a.md",
        1,
        [],
        {},
    )
    await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")

    task = client.claim_for("write-a")
    task["lease_expires_at"] = datetime(2000, 1, 1, tzinfo=UTC)
    await pages.reset_expired_leases(run_id="run-1")

    reclaimed = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-2")
    assert [t.task_id for t in reclaimed] == ["write-a"]

    with pytest.raises(StaleArtifactError):
        await pages.complete_task(
            run_id="run-1",
            task_id="write-a",
            output_data={"ok": True},
            owner="worker-1",
        )
    with pytest.raises(StaleArtifactError):
        await pages.retry_or_fail_task(
            run_id="run-1", task_id="write-a", owner="worker-1", error="boom"
        )

    assert (
        await pages.complete_task(
            run_id="run-1",
            task_id="write-a",
            output_data={"ok": True},
            owner="worker-2",
        )
        is True
    )


async def test_reset_expired_leases_recycles_only_expired_running(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    async def enqueue(task_id: str) -> None:
        await client.execute(
            """INSERT INTO documentation_workflow_tasks
                 (run_id, task_id, org_id, task_type, page_id, artifact_version,
                  dependencies, input_data, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
            "run-1",
            task_id,
            "org-1",
            "write",
            "docs/a.md",
            1,
            [],
            {},
        )

    await enqueue("write-a")
    await enqueue("write-b")
    await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-1")

    expired = client.claim_for("write-a")
    expired["lease_expires_at"] = datetime(2000, 1, 1, tzinfo=UTC)
    live = client.claim_for("write-b")

    recycled = await pages.reset_expired_leases(run_id="run-1")
    assert recycled == 1
    assert expired["status"] == "pending"
    assert expired["lease_owner"] is None
    assert live["status"] == "running"

    claimed = await pages.claim_ready_tasks(run_id="run-1", lease_owner="worker-2")
    assert [t.task_id for t in claimed] == ["write-a"]
    assert claimed[0].lease_owner == "worker-2"


async def test_get_tasks_returns_read_models_in_enqueue_order(
    client: FakeClient,
    pages: PageWorkflowRepository,
) -> None:
    async def enqueue(task_id: str) -> None:
        await client.execute(
            """INSERT INTO documentation_workflow_tasks
                 (run_id, task_id, org_id, task_type, page_id, artifact_version,
                  dependencies, input_data, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')""",
            "run-1",
            task_id,
            "org-1",
            "write",
            "docs/a.md",
            1,
            [],
            {},
        )

    await enqueue("write-a")
    await enqueue("write-b")
    client.claim_for("write-b")["created_at"] = AUCTION_EPOCH - timedelta(minutes=1)

    tasks = await pages.get_tasks(run_id="run-1")

    assert [t.task_id for t in tasks] == ["write-b", "write-a"]
    task = tasks[0]
    assert task.run_id == "run-1"
    assert task.org_id == "org-1"
    assert task.task_type == "write"
    assert task.page_id == "docs/a.md"
    assert task.artifact_version == 1
    assert task.dependencies == []
    assert task.status == "pending"
    assert task.infrastructure_retries == 0
    assert task.lease_owner is None
    assert task.lease_expires_at is None
    assert task.input_data == {}
    assert task.output_data is None


async def test_get_page_states_returns_read_models(
    client: FakeClient,
    pages: PageWorkflowRepository,
    drafts: DraftRepository,
) -> None:
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[NewPage(page_id="docs/a.md", path="docs/a.md", action="update")],
    )
    version = await pages.reserve_next_version(run_id="run-1", page_id="docs/a.md")
    sealed = await _seed_artifact(client, drafts, pages, generation=1, version=version)
    await pages.record_artifact(
        run_id="run-1",
        page_id="docs/a.md",
        artifact_id=sealed.id,
        version=version,
    )
    states = await pages.get_page_states(run_id="run-1")
    assert len(states) == 1
    state = states[0]
    assert state.run_id == "run-1"
    assert state.org_id == "org-1"
    assert state.page_id == "docs/a.md"
    assert state.path == "docs/a.md"
    assert state.action == "update"
    assert state.status == "evaluating"
    assert state.latest_artifact_id == sealed.id
    assert state.latest_version == 1
    assert state.next_version == 2
    assert state.evaluation_attempt == 0
    assert state.escalation_reason is None


async def test_create_pages_is_idempotent(
    client: FakeClient, pages: PageWorkflowRepository
) -> None:
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[NewPage(page_id="docs/a.md", path="docs/a.md", action="update")],
    )
    await pages.create_pages(
        run_id="run-1",
        org_id="org-1",
        pages=[NewPage(page_id="docs/a.md", path="docs/a.md", action="update")],
    )
    states = await pages.get_page_states(run_id="run-1")
    assert len(states) == 1
