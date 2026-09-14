from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from draftly.app.agentcore.routes import router
from draftly.app.config import Settings, get_settings
from draftly.app.lifecycle import create_application
from draftly.observability.logging import configure_logging


@asynccontextmanager
async def agentcore_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Compose Draftly workflows without the durable worker boot."""
    settings = get_settings()
    configure_logging(settings=settings)

    application = create_application(settings=settings)
    app.state.draftly = application

    try:
        await application.prepare_workflows()
        yield
    finally:
        await application.shutdown()


def create_agentcore_app(
    *,
    settings: Settings | None = None,
    with_lifespan: bool = True,
) -> FastAPI:
    """Build the AgentCore runtime FastAPI application.

    ``with_lifespan=False`` returns an app that skips composition, letting
    tests exercise routes without a database or Redis.
    """
    app = FastAPI(
        title="Draftly AgentCore",
        description="AgentCore Runtime entrypoint for Draftly workflows.",
        lifespan=agentcore_lifespan if with_lifespan else None,
    )
    app.include_router(router)
    return app
