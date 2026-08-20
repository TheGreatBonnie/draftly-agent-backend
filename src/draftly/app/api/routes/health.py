# app/api/routes/health.py

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(
    prefix="/health",
    tags=["health"],
)


@router.get("")
async def health_check(
    request: Request,
) -> dict[str, Any]:
    """
    Basic liveness check.

    This endpoint intentionally does not depend on external
    integrations being available.
    """

    return {
        "status": "ok",
        "service": "draftly",
    }


@router.get("/ready")
async def readiness_check(
    request: Request,
) -> JSONResponse:
    """
    Readiness check for infrastructure required by Draftly.
    """

    application = request.app.state.draftly

    checks: dict[str, bool] = {}

    checks["database"] = await _check_resource(
        application.dependencies.database,
    )

    checks["memory"] = await _check_resource(
        application.dependencies.memory,
    )

    checks["evaluation"] = await _check_resource(
        application.dependencies.evaluation,
    )

    ready = all(checks.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )


async def _check_resource(
    resource: object,
) -> bool:
    """
    Check whether a resource exposes a health-check method.
    """

    health_check = getattr(
        resource,
        "health_check",
        None,
    )

    if health_check is None:
        return True

    result = health_check()

    if hasattr(result, "__await__"):
        result = await result

    return bool(result)
