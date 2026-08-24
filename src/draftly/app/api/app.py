# app/api/app.py

from fastapi import FastAPI

from draftly.app.api.middleware.logging import RequestLoggingMiddleware
from draftly.app.api.routes import (
    clerk,
    discord,
    documentation,
    evaluations,
    github,
    health,
    jobs,
    metrics,
    observability,
    onboarding,
    reviewers,
    reviews,
    runs,
    slack,
    support,
    workflows,
)
from draftly.app.lifecycle import lifespan


def create_api_app() -> FastAPI:
    """
    Create the Draftly FastAPI application.
    """

    app = FastAPI(
        title="Draftly",
        description=("Autonomous documentation engineering platform."),
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)

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

    app.include_router(
        workflows.router,
        prefix="/api",
    )

    app.include_router(
        observability.router,
        prefix="/api",
    )

    app.include_router(
        onboarding.router,
        prefix="/api",
    )

    app.include_router(
        metrics.router,
        prefix="/api",
    )

    app.include_router(
        reviews.router,
        prefix="/api",
    )

    app.include_router(
        runs.router,
        prefix="/api",
    )

    return app


app = create_api_app()
