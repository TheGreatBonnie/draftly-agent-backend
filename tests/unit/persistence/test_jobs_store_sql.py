"""JobsStore SQL contract tests.

There is no real-Postgres harness in this suite, so these tests capture the
exact SQL emitted by DatabaseJobsStore via a scripted client and assert the
runtime contract that Postgres/asyncpg enforce at prepare time: for every
statement, the number of positional placeholders ($1..$N) must equal the
number of columns in the INSERT column list and the number of arguments
supplied.  A mismatch here would make the reconcile/insert never run, so
o/stream-ticket would 404 and onboarding.initialize would 500.

A follow-up against a real Postgres is tracked separately.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.jobs_store import DatabaseJobsStore


@dataclass
class ScriptedClient:
    """Returns scripted rows in order and records every (sql, args) pair."""

    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.calls.append((" ".join(query.split()), args))
        return self.responses.pop(0) if self.responses else None

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((" ".join(query.split()), args))
        return []


def _placeholder_count(sql: str) -> int:
    return max(int(n) for n in re.findall(r"\$(\d+)", sql))


def _insert_column_count(sql: str) -> int:
    m = re.search(r"INSERT INTO \w+\s*\((.*?)\)\s*VALUES", sql)
    assert m, f"no INSERT column list found in: {sql}"
    return len([c for c in m.group(1).split(",") if c.strip()])


def _assert_placeholders_match(sql: str, args: tuple[Any, ...]) -> None:
    placeholders = _placeholder_count(sql)
    num_args = len(args)
    assert placeholders == num_args, (
        f"placeholder count {placeholders} != arg count {num_args}: {sql}"
    )
    # Configured inside the scripted client we still get the same SQL/args in
    # the same call, so assert the placeholder/column contract for inserts.
    if sql.startswith("INSERT INTO jobs"):
        assert placeholders == _insert_column_count(sql), (
            f"placeholder count {placeholders} != insert column count "
            f"{_insert_column_count(sql)}: {sql}"
        )


def _capture(responses: list[Any], calls: list) -> DatabaseJobsStore:
    calls.clear()
    client = ScriptedClient(responses=responses, calls=calls)
    return DatabaseJobsStore(client=cast(DatabaseClient, client))


def _row() -> dict[str, Any]:
    return {
        "id": "job-1",
        "run_id": "run-1",
        "org_id": "org-1",
        "name": "onboarding.initialize",
        "job_type": "onboarding",
        "schedule": "manual",
        "status": "pending",
        "configuration": {},
        "last_run_at": None,
        "next_run_at": None,
    }


async def test_insert_placeholder_count_matches_columns_and_args():
    calls: list[tuple[str, tuple]] = []
    store = _capture([_row()], calls)

    await store.insert(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={"rq_job_id": "abc"},
        status="pending",
    )

    sql, args = calls[-1]
    assert sql.startswith("INSERT INTO jobs")
    _assert_placeholders_match(sql, args)
    assert args[1] == "run-1"
    assert args[2] == "org-1"
    assert args[7] == json.dumps({"rq_job_id": "abc"})


async def test_upsert_on_conflict_placeholder_count_matches_columns_and_args():
    calls: list[tuple[str, tuple]] = []
    store = _capture([None], calls)

    await store.upsert_on_conflict(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={},
        status="pending",
    )

    sql, args = calls[-1]
    assert sql.startswith("INSERT INTO jobs")
    assert "ON CONFLICT (run_id) DO NOTHING" in sql
    _assert_placeholders_match(sql, args)


async def test_insert_and_upsert_use_identical_column_placeholder_shape():
    insert_calls: list[tuple[str, tuple]] = []
    upsert_calls: list[tuple[str, tuple]] = []

    ins = _capture([_row()], insert_calls)
    ups = _capture([None], upsert_calls)

    await ins.insert(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={},
        status="pending",
    )
    await ups.upsert_on_conflict(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={},
        status="pending",
    )

    insert_sql, insert_args = insert_calls[-1]
    upsert_sql, upsert_args = upsert_calls[-1]

    assert _placeholder_count(insert_sql) == len(insert_args)
    assert _placeholder_count(upsert_sql) == len(upsert_args)
    assert _insert_column_count(insert_sql) == _insert_column_count(upsert_sql)


async def test_configuration_is_passed_as_serialized_json_not_raw_dict():
    """Regression: asyncpg rejects a raw dict for a jsonb column
    (DataError: invalid input for query argument $8: expected str, got dict),
    so the store must bind a JSON-serialized string. The $8::JSONB cast alone
    does not change asyncpg's client-side codec selection for a dict value.
    Reproduced against a real Neon (Postgres) database.
    """
    calls: list[tuple[str, tuple]] = []
    store = _capture([_row()], calls)
    cfg = {"rq_job_id": "58a28b20-c028-4d15-868b-c26994d160f9", "nested": {"k": 1}}

    await store.insert(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration=cfg,
        status="pending",
    )

    sql, args = calls[-1]
    assert "$8::JSONB" in sql
    config_arg = args[7]
    assert isinstance(config_arg, str), (
        f"configuration bound as {type(config_arg).__name__}, expected str"
    )
    assert json.loads(config_arg) == cfg


async def test_upsert_configuration_is_passed_as_serialized_json():
    calls: list[tuple[str, tuple]] = []
    store = _capture([None], calls)
    cfg = {"rq_job_id": "x"}

    await store.upsert_on_conflict(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration=cfg,
        status="pending",
    )

    sql, args = calls[-1]
    assert "$8::JSONB" in sql
    assert isinstance(args[7], str)
    assert json.loads(args[7]) == cfg


async def test_update_status_persists_terminal_lifecycle_fields():
    calls: list[tuple[str, tuple]] = []
    store = _capture([_row()], calls)

    await store.update_status(
        job_id="run-1",
        status="failed",
        error="graph failed",
        result={"status": "FAILED"},
    )

    sql, args = calls[-1]
    assert "completed_at = CASE" in sql
    assert "started_at = CASE" in sql
    assert "error = $" in sql
    assert "result = $" in sql
    assert args[0] == "failed"
    assert args[1] == "run-1"
    assert args[-2] == "graph failed"
    assert json.loads(args[-1]) == {"status": "FAILED"}


async def test_list_active_filters_by_org_id_when_requested():
    calls: list[tuple[str, tuple]] = []
    store = _capture([], calls)

    rows = await store.list_active(org_id="org-a")

    assert rows == []
    sql, args = calls[-1]
    assert "WHERE status = 'active'" in sql
    assert "AND org_id = $1" in sql
    assert args == ("org-a",)


async def test_list_active_without_org_id_preserves_unfiltered_query():
    calls: list[tuple[str, tuple]] = []
    store = _capture([], calls)

    rows = await store.list_active()

    assert rows == []
    sql, args = calls[-1]
    assert "WHERE status = 'active'" in sql
    assert "AND org_id = $1" not in sql
    assert args == ()


_POSITIONAL_ROW = (
    "job-1",
    "run-1",
    "org-1",
    "onboarding.initialize",
    "onboarding",
    "manual",
    "pending",
    '{"rq_job_id": "abc"}',
    None,
    None,
)


async def test_insert_handles_positional_returning_row_like_asyncpg_record():
    """DatabaseClient.fetch_one returns an asyncpg.Record (positional), not a
    dict. _to_dict maps it by index, so the RETURNING column list must expose
    exactly the 10 fields _to_dict reads (id..next_run_at). Regression for a
    real-DB IndexError: 'record index out of range' when insert/upsert
    RETURNING only 8 columns.
    """
    calls: list[tuple[str, tuple]] = []
    store = _capture([tuple(_POSITIONAL_ROW)], calls)

    row = await store.insert(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={"rq_job_id": "abc"},
        status="pending",
    )

    sql, _ = calls[-1]
    returning = sql.split("RETURNING", 1)[1]
    cols = [c.strip() for c in returning.split(",") if c.strip()]
    assert [c for c in cols if c] == [
        "id",
        "run_id",
        "org_id",
        "name",
        "job_type",
        "schedule",
        "status",
        "configuration",
        "last_run_at",
        "next_run_at",
    ]
    assert row == {
        "id": "job-1",
        "run_id": "run-1",
        "org_id": "org-1",
        "name": "onboarding.initialize",
        "job_type": "onboarding",
        "schedule": "manual",
        "status": "pending",
        "configuration": '{"rq_job_id": "abc"}',
        "last_run_at": None,
        "next_run_at": None,
    }


async def test_upsert_handles_positional_returning_row_like_asyncpg_record():
    calls: list[tuple[str, tuple]] = []
    store = _capture([tuple(_POSITIONAL_ROW)], calls)

    row = await store.upsert_on_conflict(
        run_id="run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={"rq_job_id": "abc"},
        status="pending",
    )

    sql, _ = calls[-1]
    returning = sql.split("RETURNING", 1)[1]
    cols = [c.strip() for c in returning.split(",") if c.strip()]
    assert cols == [
        "id",
        "run_id",
        "org_id",
        "name",
        "job_type",
        "schedule",
        "status",
        "configuration",
        "last_run_at",
        "next_run_at",
    ]
    assert row["run_id"] == "run-1"
    assert row["last_run_at"] is None
