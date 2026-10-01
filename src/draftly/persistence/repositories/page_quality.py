"""Org-wide page quality aggregates over the latest artifact for each page.

The join here is the same one the reviews list and detail use, so the section
total and the per-review column cannot disagree.
"""

from __future__ import annotations

from typing import Any

from draftly.integrations.database.client import DatabaseClient

# The FROM/JOIN only, so callers can append their own LATERAL joins and then
# apply _SCOPED. Kept separate because a constant that already closed with a
# WHERE clause cannot legally take another join.
_LATEST_PAGES_JOIN = """
    FROM documentation_page_states AS s
    JOIN documentation_page_evaluations AS e
      ON e.run_id = s.run_id
     AND e.artifact_id = s.latest_artifact_id
"""

# Org and window scoping, appended last.
_SCOPED = """
    WHERE s.org_id = $1
      AND e.created_at >= NOW() - ($2 || ' days')::interval
"""


def _pct(value: Any) -> float | None:
    """Scores are stored 0-1; the UI shows 0-100. None stays None."""
    if value is None:
        return None
    return round(float(value) * 100, 1)


class PageQualityRepository:
    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def summary(self, org_id: str, days: int) -> dict[str, Any]:
        row = await self.database.fetch_one(
            f"""
            SELECT
                avg(e.score) AS average_score,
                count(DISTINCT e.run_id) AS total_runs,
                count(*) FILTER (WHERE e.score IS NOT NULL) AS scored_pages,
                count(*) AS total_pages,
                count(*) FILTER (WHERE e.status = 'passed') AS passed,
                count(*) FILTER (WHERE e.status = 'revision_required') AS needs_revision,
                count(*) FILTER (WHERE e.status = 'awaiting_human_review') AS awaiting_human
            {_LATEST_PAGES_JOIN}
            {_SCOPED}
            """,
            org_id,
            str(days),
        )
        row = row or {}
        metrics = await self.database.fetch_all(
            f"""
            SELECT
                m->>'name' AS metric,
                avg((m->>'score')::float) AS average_score,
                count(*) AS sample_count
            {_LATEST_PAGES_JOIN},
                 LATERAL jsonb_array_elements(e.metrics) AS m
            {_SCOPED}
              AND m->>'name' IS NOT NULL
            GROUP BY m->>'name'
            ORDER BY sample_count DESC
            """,
            org_id,
            str(days),
        )
        trend = await self.database.fetch_all(
            f"""
            SELECT
                e.created_at::date AS date,
                avg(e.score) AS average_score,
                count(DISTINCT e.run_id) AS run_count
            {_LATEST_PAGES_JOIN}
            {_SCOPED}
              AND e.score IS NOT NULL
            GROUP BY e.created_at::date
            ORDER BY e.created_at::date
            """,
            org_id,
            str(days),
        )
        return {
            "average_score": _pct(row.get("average_score")),
            "total_runs": int(row.get("total_runs") or 0),
            "scored_pages": int(row.get("scored_pages") or 0),
            "total_pages": int(row.get("total_pages") or 0),
            "passed": int(row.get("passed") or 0),
            "needs_revision": int(row.get("needs_revision") or 0),
            "awaiting_human": int(row.get("awaiting_human") or 0),
            "by_metric": [
                {
                    "metric": str(m.get("metric")),
                    "average_score": _pct(m.get("average_score")),
                    "sample_count": int(m.get("sample_count") or 0),
                }
                for m in metrics
            ],
            "trend": [
                {
                    "date": str(t.get("date")),
                    "average_score": _pct(t.get("average_score")),
                    "run_count": int(t.get("run_count") or 0),
                }
                for t in trend
            ],
        }

    async def pages(
        self,
        org_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Page evaluations across every run, worst score first.

        The cursor is ``"<score>:<page_id>"`` from the previous page, so paging
        is a keyset walk on (score, page_id) and stays stable while scores move.
        Unscored pages sort last: unknown is not the worst.
        """
        clamped = max(1, min(limit, 200))
        params: list[Any] = [org_id, clamped + 1]
        cursor_clause = ""
        if cursor:
            raw_score, _, page_id = cursor.partition(":")
            if raw_score in ("", "null", "None"):
                cursor_clause = "AND e.score IS NULL AND s.page_id > $3"
                params.append(page_id)
            else:
                cursor_clause = "AND (e.score, s.page_id) < ($3::float, $4)"
                params.extend([raw_score, page_id])
        rows = await self.database.fetch_all(
            f"""
            SELECT
                s.page_id,
                s.path,
                e.status,
                e.score,
                e.run_id,
                s.updated_at
            {_LATEST_PAGES_JOIN}
            WHERE s.org_id = $1
              {cursor_clause}
            ORDER BY e.score ASC NULLS LAST, s.page_id ASC
            LIMIT $2
            """,
            *params,
        )
        has_more = len(rows) > clamped
        items = rows[:clamped]
        next_cursor = None
        if has_more and items:
            last_score = items[-1].get("score")
            last_score_token = "null" if last_score is None else str(last_score)
            next_cursor = f"{last_score_token}:{items[-1].get('page_id')}"
        return (
            [
                {
                    "page_id": str(r.get("page_id")),
                    "path": r.get("path"),
                    "status": r.get("status"),
                    "score": _pct(r.get("score")),
                    "run_id": str(r.get("run_id")),
                    "updated_at": r.get("updated_at"),
                }
                for r in items
            ],
            len(items),
            next_cursor,
        )
