"""DatabaseEvaluationsStore SQL contract tests.

There is no real-Postgres harness in this suite, so these tests capture the
exact SQL emitted by DatabaseEvaluationsStore via a scripted client and assert
the runtime contract that Postgres/asyncpg enforce at prepare time: for every
INSERT, the number of positional placeholders ($1..$N) must equal the number of
columns in the INSERT column list and the number of arguments supplied. A
mismatch here would make evaluation summaries never persist.

Follows the pattern of test_jobs_store_sql.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.evaluations_store import DatabaseEvaluationsStore


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
    return len(_insert_column_list(sql))


def _insert_column_list(sql: str) -> list[str]:
    m = re.search(r"INSERT INTO \w+\s*\((.*?)\)\s*VALUES", sql)
    assert m, f"no INSERT column list found in: {sql}"
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


def _assert_placeholders_match(sql: str, args: tuple[Any, ...]) -> None:
    placeholders = _placeholder_count(sql)
    num_args = len(args)
    assert placeholders == num_args, (
        f"placeholder count {placeholders} != arg count {num_args}: {sql}"
    )
    if sql.startswith("INSERT INTO evaluations"):
        assert placeholders == _insert_column_count(sql), (
            f"placeholder count {placeholders} != insert column count "
            f"{_insert_column_count(sql)}: {sql}"
        )


def _capture(responses: list[Any], calls: list) -> DatabaseEvaluationsStore:
    calls.clear()
    client = ScriptedClient(responses=responses, calls=calls)
    return DatabaseEvaluationsStore(client=cast(DatabaseClient, client))


def _row() -> tuple[Any, ...]:
    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    return (
        "ev-1",
        "org-1",
        "documentation",
        "run-1",
        "oauth-auth",
        "documentation",
        None,
        1.0,
        True,
        "passed",
        {},
        [],
        "tr-1",
        started,
        started,
    )


async def test_insert_placeholder_count_matches_columns_and_args() -> None:
    calls: list[tuple[str, tuple]] = []
    store = _capture([_row()], calls)

    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    await store.insert(
        org_id="org-1",
        evaluation_type="documentation",
        run_id="run-1",
        case_id="oauth-auth",
        target_type="documentation",
        target_id=None,
        score=1.0,
        passed=True,
        status="passed",
        metrics={},
        failures=[],
        trace_id="tr-1",
        started_at=started,
        completed_at=started,
    )

    sql, args = calls[0]
    _assert_placeholders_match(sql, args)
    for column in (
        "case_id",
        "target_type",
        "passed",
        "trace_id",
    ):
        assert column in sql, f"insert SQL missing column {column}: {sql}"
    columns = _insert_column_list(sql)
    assert "metric" not in columns, f"insert SQL still contains metric: {sql}"
    assert "threshold" not in columns, f"insert SQL still contains threshold: {sql}"


def _row_with_string_jsonb() -> tuple[Any, ...]:
    """A SELECT row where JSONB columns arrive as raw JSON strings.

    This is what the store sees when asyncpg's default ``jsonb`` codec is used
    (no decoder registered): the ``metrics``/``failures`` columns decode to
    text instead of dict/list.
    """
    started = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    metrics = {
        "cases": 14,
        "passed": 14,
        "failed": 0,
        "granular": [
            {
                "case": "oauth-authentication-add",
                "metric": "groundedness",
                "score": 1,
                "test_pass": True,
                "reason": "fully grounded",
            }
        ],
    }
    return (
        "ev-1",
        "org-1",
        "documentation",
        "run-1",
        None,  # case_id
        "documentation",  # target_type
        None,  # target_id
        100.0,  # score
        True,  # passed
        "passed",  # status
        json.dumps(metrics),  # metrics (JSON string!)
        json.dumps([]),  # failures (JSON string!)
        "tr-1",  # trace_id
        started,
        started,
    )


async def test_get_decodes_string_typed_jsonb_metrics_and_failures() -> None:
    """_to_dict must turn string-typed JSONB columns back into containers.

    Regression guard: previously the store returned ``metrics`` as the raw JSON
    string, so the API relayed it to the frontend as a string and the detail
    page showed no metrics (no granular rows, zeroed counts). Even after the
    connection-level codec fix, keep this defense-in-depth so a string can
    never leak to consumers.
    """
    store = _capture([_row_with_string_jsonb()], [])

    item = await store.get(evaluation_id="ev-1")

    assert item is not None
    assert isinstance(item["metrics"], dict), (
        f"expected metrics decoded to dict, got {type(item['metrics']).__name__}: "
        f"{item['metrics']!r}"
    )
    assert item["metrics"]["cases"] == 14
    assert item["metrics"]["granular"][0]["metric"] == "groundedness"
    assert isinstance(item["failures"], list)
