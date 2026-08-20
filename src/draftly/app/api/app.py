# app/api/app.py

from fastapi import FastAPI  # ty: ignore[unresolved-import]

from draftly.app.api.routes import (
    clerk,
    discord,
    documentation,
    evaluations,
    github,
    health,
    jobs,
    reviewers,
    slack,
    support,
)
from draftly.app.lifecycle import lifespan


def create_api_app() -> FastAPI:
    """
    Create the Draftly FastAPI application.
    """

    app = FastAPI(
        title="Draftly",
        description=(
            "Autonomous documentation engineering platform."
        ),
        lifespan=lifespan,
    )

    app.include_router(
        health.router,
        prefix="/api",
    )

    app.include_router(
        github.router,
        prefix="/api",
    )

    app.include_router(
        slack.router,
        prefix="/api",
    )

    app.include_router(
        discord.router,
        prefix="/api",
    )

    app.include_router(
        documentation.router,
        prefix="/api",
    )

    app.include_router(
        support.router,
        prefix="/api",
    )

    app.include_router(
        evaluations.router,
        prefix="/api",
    )

    app.include_router(
        jobs.router,
        prefix="/api",
    )

    app.include_router(
        clerk.router,
        prefix="/api",
    )

    app.include_router(
        reviewers.router,
        prefix="/api",
    )

    return app


app = create_api_app()
