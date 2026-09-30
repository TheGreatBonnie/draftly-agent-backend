"""The review queue is read newest-first, and its keyset cursor must agree.

`list_reviews` sorts and paginates on the same `(created_at, id)` key. Those two
have to walk the same direction: if the sort flips to newest-first but the
cursor still asks for rows *after* the last one, page 2 re-serves page 1
forever. The tests below pin the emitted SQL and the invariant that keeps the
two in step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from draftly.persistence.repositories.reviews import (
    ReviewsRepository,
    _decode_cursor,
    _encode_cursor,
    review_page_clauses,
)

OLDEST = datetime(2026, 9, 28, 4, 42, tzinfo=UTC)
MIDDLE = datetime(2026, 9, 28, 7, 33, tzinfo=UTC)
NEWEST = datetime(2026, 9, 28, 8, 37, tzinfo=UTC)


class FakeClient:
    """Records queries and emulates the keyset SELECT well enough to walk pages.

    `FakeDatabase` in tests.fakes returns every row for a table regardless of
    ORDER BY, WHERE or LIMIT, so it cannot show whether pagination advances.
    This applies the same three clauses for the reviews query only.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.rows = rows or []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        return None

    async def fetch_all(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self._reviews_select(query, args) if "FROM reviews" in query else []

    def _reviews_select(self, query: str, args: tuple) -> list[dict[str, Any]]:
        rows = sorted(self.rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
        if "(created_at, id) < (" in query:
            at, rid = args[1], args[2]
            rows = [r for r in rows if (r["created_at"], r["id"]) < (at, rid)]
        elif "(created_at, id) > (" in query:
            at, rid = args[1], args[2]
            rows = [r for r in rows if (r["created_at"], r["id"]) > (at, rid)]
        if "LIMIT" in query:
            # The placeholder index tells us which arg carries the limit.
            limit = int(query.split("LIMIT")[1].strip().lstrip("$"))
            rows = rows[: int(args[limit - 1])]
        return rows

    @property
    def select(self) -> str:
        return self.executed[0][0]


def row(review_id: str, created_at: datetime) -> dict[str, Any]:
    return {
        "id": review_id,
        "org_id": "org-1",
        "thread_id": f"run-{review_id}",
        "workflow": "documentation",
        "tool_name": "doc-review",
        "tool_args": {},
        "action_description": None,
        "status": "pending",
        "reviewer_id": None,
        "decision": None,
        "decision_comment": None,
        "decided_at": None,
        "created_at": created_at,
        "detail": {},
    }


async def test_list_reviews_sorts_newest_first() -> None:
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.list_reviews(org_id="org-1")

    assert "ORDER BY created_at DESC, id DESC" in client.select


async def test_list_reviews_cursor_asks_for_older_rows() -> None:
    """The cursor must walk the same way the sort does, or pagination loops."""
    client = FakeClient()
    repo = ReviewsRepository(database=client)

    await repo.list_reviews(
        org_id="org-1",
        cursor=_encode_cursor(
            type(
                "Cursor",
                (),
                {"created_at": NEWEST, "id": "review-b"},
            )()
        ),
    )

    assert "(created_at, id) < (" in client.select
    assert "ORDER BY created_at DESC, id DESC" in client.select


def test_the_cursor_comparison_always_walks_the_same_way_as_the_sort() -> None:
    """The structural guard: sort direction and cursor comparison are derived
    together, so they cannot drift apart when one of them is edited."""
    expected = {"ASC": ">", "DESC": "<"}
    for direction, comparison in expected.items():
        order, cursor = review_page_clauses(direction)
        for term in order.removeprefix("ORDER BY ").split(","):
            assert term.strip().endswith(direction), f"{order!r} disagrees with {direction}"
        assert cursor == comparison


async def test_paging_through_the_queue_visits_every_review_exactly_once() -> None:
    """The end-to-end guarantee: newest-first, no repeats, no gaps, and stable
    when two reviews share a timestamp. Flipping only the ORDER BY breaks this."""
    # review-d and review-b share a timestamp, so the id tiebreak is load-bearing.
    rows = [
        row("review-a", OLDEST),
        row("review-c", MIDDLE),
        row("review-d", NEWEST),
        row("review-b", NEWEST),
    ]
    client = FakeClient(rows=rows)
    repo = ReviewsRepository(database=client)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(len(rows) + 1):
        page = await repo.list_reviews_page(org_id="org-1", limit=2, cursor=cursor)
        seen.extend(item.id for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert seen == ["review-d", "review-b", "review-c", "review-a"]
    assert cursor is None  # the walk terminated rather than looping


async def test_list_reviews_page_resumes_from_the_oldest_row_it_returned() -> None:
    """Newest-first means the last row of a page is the oldest one shown, and
    that row is the resume point."""
    client = FakeClient(rows=[row("review-b", OLDEST), row("review-c", NEWEST)])
    repo = ReviewsRepository(database=client)

    page = await repo.list_reviews_page(org_id="org-1", limit=1)

    assert [item.id for item in page["items"]] == ["review-c"]
    assert page["next_cursor"] is not None
    # Decode the cursor the API hands the client and confirm the resume point.
    created_at, review_id = _decode_cursor(page["next_cursor"])
    assert (created_at, review_id) == (NEWEST, "review-c")
