"""Process metrics exposition (spec §Observability surface #3).

Caveat documented in the spec: registries are process-local; this endpoint
reflects the serving process only (API container ≠ worker container).
Prometheus scrapers should target each container separately.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from draftly.observability.metrics import metrics

router = APIRouter(prefix="/metrics", tags=["metrics"], include_in_schema=False)


@router.get("")
async def prometheus() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


@router.get("/snapshot")
async def snapshot() -> dict:
    return metrics.snapshot()
