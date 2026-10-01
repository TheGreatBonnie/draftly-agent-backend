"""The cross-run page list: worst score first, keyset-paginated."""

from __future__ import annotations

import pytest

from draftly.persistence.repositories.page_quality import PageQualityRepository


class PagesClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch_all(self, query: str, *args):
        self.calls.append((query, args))
        return list(self.rows)


def row(**overrides) -> dict:
    base = {
        "page_id": "p1",
        "path": "docs/a.md",
        "status": "revision_required",
        "score": 0.4,
        "run_id": "run-1",
        "updated_at": "2026-09-28T00:00:00Z",
    }
    base.update(overrides)
    return base


async def test_pages_returns_rows_with_page_identity_and_percentages() -> None:
    client = PagesClient([row()])
    repo = PageQualityRepository(client)

    rows, total, next_cursor = await repo.pages("org-1", limit=20)

    assert total == 1
    assert next_cursor is None
    assert rows[0]["page_id"] == "p1"
    assert rows[0]["path"] == "docs/a.md"
    assert rows[0]["score"] == pytest.approx(40.0)
    assert rows[0]["run_id"] == "run-1"

    sql, params = client.calls[-1]
    assert "org_id = $1" in sql
    assert "org-1" in params
    # Worst score first. The cursor predicate must match the sort direction:
    # ASC pairs with `<`. If either is flipped without the other, paging skips
    # or repeats rows.
    assert "ORDER BY e.score ASC NULLS LAST" in sql


async def test_pages_keeps_unscored_pages_as_null_not_zero() -> None:
    client = PagesClient([row(score=None, status="awaiting_human_review")])
    repo = PageQualityRepository(client)

    rows, _total, _cursor = await repo.pages("org-1", limit=20)

    # Unknown is not 0% -- the UI must render an em dash.
    assert rows[0]["score"] is None


async def test_pages_paginates_and_reports_a_cursor() -> None:
    # Fake returns limit+1 rows so the repository detects a further page.
    rows = [row(page_id=f"p{i}", path=f"docs/{i}.md") for i in range(3)]
    client = PagesClient(rows)
    repo = PageQualityRepository(client)

    items, total, next_cursor = await repo.pages("org-1", limit=2)

    assert len(items) == 2
    assert total == 2
    assert next_cursor == "0.4:p1:run-1"
    # Fetched one extra row to detect the next page.
    sql, params = client.calls[-1]
    assert params[1] == 3


async def test_pages_applies_the_cursor_predicate() -> None:
    client = PagesClient([row(page_id="p2")])
    repo = PageQualityRepository(client)

    await repo.pages("org-1", limit=20, cursor="0.3:p0:run-9")

    sql, params = client.calls[-1]
    # The run_id tiebreak is load-bearing: the same page is evaluated in many
    # runs, so (score, page_id) alone is not a total order and the walk would
    # skip same-page siblings.
    assert "(e.score, s.page_id, e.run_id) >" in sql
    assert "ORDER BY e.score ASC NULLS LAST, s.page_id ASC, e.run_id ASC" in sql
    # Typed as a float in Python: asyncpg refuses str for a float slot.
    assert params[2] == pytest.approx(0.3)
    assert isinstance(params[2], float)
    assert params[3] == "p0"
    assert params[4] == "run-9"


async def test_pages_rejects_a_malformed_cursor() -> None:
    client = PagesClient([])
    repo = PageQualityRepository(client)

    with pytest.raises(ValueError, match="invalid cursor"):
        await repo.pages("org-1", limit=20, cursor="not-a-score:p0:run-1")
    with pytest.raises(ValueError, match="invalid cursor"):
        await repo.pages("org-1", limit=20, cursor="0.3:p0")


async def test_pages_clamps_the_limit() -> None:
    client = PagesClient([])
    repo = PageQualityRepository(client)

    await repo.pages("org-1", limit=5000)

    _sql, params = client.calls[-1]
    assert params[1] == 201


async def test_pages_walks_the_unscored_tail_by_page_id() -> None:
    client = PagesClient([row(page_id="p9", score=None)])
    repo = PageQualityRepository(client)

    await repo.pages("org-1", limit=20, cursor="null:p1:run-1")

    sql, params = client.calls[-1]
    assert "e.score IS NULL" in sql
    assert params[2] == "p1"


async def test_pages_emits_a_null_cursor_for_an_unscored_last_row() -> None:
    rows = [row(page_id="p1", score=None), row(page_id="p2", score=None)]
    client = PagesClient(rows)
    repo = PageQualityRepository(client)

    _items, _total, next_cursor = await repo.pages("org-1", limit=1)

    assert next_cursor == "null:p1:run-1"
