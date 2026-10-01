"""The overview snapshot must satisfy the route's declared response model.

The service and the route schema live in different modules and drifted apart
when evaluation status moved onto page quality. These tests pin the contract
so a status the service can emit can never fail response validation.
"""

from __future__ import annotations

import pytest

from draftly.app.api.routes.overview import OverviewSystem
from draftly.app.services import overview


def _page_quality_summary(**overrides: object) -> dict[str, object]:
    summary: dict[str, object] = {
        "total_pages": 4,
        "needs_revision": 0,
        "awaiting_human": 0,
        "average_score": 62.4,
        "by_metric": [],
        "trend": [],
    }
    summary.update(overrides)
    return summary


def _statuses_the_service_can_emit() -> set[str]:
    cases = [
        _page_quality_summary(total_pages=0),
        _page_quality_summary(needs_revision=1),
        _page_quality_summary(awaiting_human=1),
        _page_quality_summary(),
    ]
    return {overview._page_quality_evaluation_summary(case)[2] for case in cases}


def test_service_statuses_are_all_valid_response_model_values() -> None:
    statuses = _statuses_the_service_can_emit()

    assert "Needs review" in statuses
    for status in sorted(statuses):
        OverviewSystem(
            agents_online=1,
            agents_total=1,
            data_sources_connected=1,
            data_sources_total=3,
            evaluations_status=status,
            scheduler_status="Idle",
        )


@pytest.mark.parametrize(
    ("page_quality", "expected"),
    [
        (_page_quality_summary(total_pages=0), "Unknown"),
        (_page_quality_summary(needs_revision=1), "Needs review"),
        (_page_quality_summary(awaiting_human=1), "Needs review"),
        (_page_quality_summary(), "Idle"),
    ],
)
def test_needs_work_status_validates_against_route_schema(
    page_quality: dict[str, object], expected: str
) -> None:
    _, _, status = overview._page_quality_evaluation_summary(page_quality)

    assert status == expected
    assert OverviewSystem.model_validate(
        {
            "agents_online": 1,
            "agents_total": 1,
            "data_sources_connected": 1,
            "data_sources_total": 3,
            "evaluations_status": status,
            "scheduler_status": "Healthy",
        }
    ).evaluations_status == expected
