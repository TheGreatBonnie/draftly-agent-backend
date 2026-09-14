from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["agentcore"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    """AgentCore-required liveness endpoint (GET /ping)."""
    return {"status": "healthy"}
