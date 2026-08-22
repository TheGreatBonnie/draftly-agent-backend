# app/lifecycle.py

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from draftly.app.composition.agents import AgentRegistry, build_agents
from draftly.app.composition.events import EventComposition, build_event_system
from draftly.app.composition.tools import ToolRegistry, build_tools
from draftly.app.composition.workflows import ComposedWorkflows, build_workflows
from draftly.app.config import Settings, get_settings
from draftly.app.dependencies import (
    ApplicationDependencies,
    build_dependencies,
)
from draftly.app.workers.worker import DraftlyWorker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DraftlyApplication:
    """
    Fully composed Draftly runtime.

    This object owns the infrastructure, tools, agents,
    workflows, and event system used by the application.
    """

    settings: Settings
    dependencies: ApplicationDependencies

    tools: ToolRegistry
    agents: AgentRegistry | None = None
    workflows: ComposedWorkflows | None = None
    events: EventComposition | None = None

    worker: DraftlyWorker | None = None
    session_manager: Any = None

    review_notification: Any = None
    review_decision: Any = None

    _started: bool = False
    _gateway_task: Any = None
    _slack_task: Any = None

    async def startup(self) -> None:
        """
        Start the Draftly runtime.

        Startup order:

            infrastructure
                ↓
            agents + workflows
                ↓
            worker
        """

        if self._started:
            return

        # Mark as started before booting so that a partial failure
        # still triggers cleanup in shutdown().
        self._started = True

        try:
            await self._start_infrastructure()

            # Build agents and workflows after checkpointer is ready
            await self._build_agents_and_workflows()

            # Start Discord Gateway if bot token is configured
            if self.settings.discord_bot_token:
                gateway = self.dependencies.integrations.discord_gateway
                if gateway is not None:
                    self._gateway_task = asyncio.create_task(gateway.start())
                    logger.info("discord_gateway_started")

            # Start Slack Bolt app if configured
            from draftly.integrations.slack.socket import should_use_socket_mode

            if should_use_socket_mode():
                from draftly.integrations.slack.socket import start_socket_mode

                self._slack_task = asyncio.create_task(start_socket_mode())
                logger.info("slack_socket_mode_started")

            if self.worker is not None:
                await self.worker.start()

        except Exception:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        """
        Gracefully shut down the Draftly runtime.

        Shutdown occurs in reverse startup order so background
        work stops before infrastructure is closed.
        """

        if not self._started:
            return

        try:
            if self.worker is not None:
                await self.worker.stop()
        except Exception:
            logger.exception("Failed to stop worker during shutdown")
        finally:
            # Stop Slack Bolt app
            if self._slack_task is not None:
                try:
                    self._slack_task.cancel()
                    await self._slack_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("slack_socket_mode_stop_failed")
                finally:
                    self._slack_task = None
            # Stop Discord Gateway
            if self._gateway_task is not None:
                try:
                    self._gateway_task.cancel()
                    await self._gateway_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("discord_gateway_stop_failed")
                finally:
                    self._gateway_task = None
            try:
                await self._stop_infrastructure()
            except Exception:
                logger.exception("Failed to stop infrastructure during shutdown")
            finally:
                self._started = False

    # ========================================================
    # Infrastructure
    # ========================================================

    async def _start_infrastructure(self) -> None:
        """
        Start long-lived infrastructure services.
        """

        await self._maybe_start(
            self.dependencies.database,
        )

        # await self._maybe_start(
        #     self.dependencies.memory,
        # )

        await self._maybe_start(
            self.dependencies.evaluation,
        )

        # Session manager (Strands) is initialized in Phase 4 once
        # the session store is implemented.

    async def _build_agents_and_workflows(self) -> None:
        """Build agents and workflows after session manager is ready."""
        self.agents = build_agents(
            models=self.dependencies.models,
            tools=self.tools,
            session_manager=self.session_manager,
        )

        memory_service = self._build_memory_service()
        feedback_service = self._build_feedback_service()
        audit_repo = self._build_audit_repo()

        self.workflows = build_workflows(
            agents=self.agents,
            tools=self.tools,
            repositories=self.dependencies.repositories,
            memory=memory_service,
            evaluation=self.dependencies.evaluation,
            feedback=feedback_service,
            config=self.settings,
            model=self._resolve_runtime_model(),
            hooks=[],
            audit_repo=audit_repo,
        )

        self.events = build_event_system(
            workflows=self.workflows,
        )

        # Build worker after workflows are ready
        if self.settings.worker_enabled:
            from draftly.app.composition.workers import (
                build_scheduler_client,
                build_task_runner,
                build_worker,
            )

            task_runner = build_task_runner(
                workflows=self.workflows,
                dependencies=self.dependencies,
            )

            scheduler_client = build_scheduler_client(
                task_runner=task_runner,
            )

            self.worker = build_worker(
                task_runner=task_runner,
                scheduler_client=scheduler_client,
            )

    def _build_memory_service(self) -> Any:
        """Build the §8.1 memory service over the persistence repo."""
        try:
            from draftly.memory import DomainMemoryRepository, MemoryService

            return MemoryService(
                repository=DomainMemoryRepository(self.dependencies.repositories.memory)
            )
        except Exception as exc:
            logger.warning("memory_service_unavailable: %s", exc)
            return None

    def _build_feedback_service(self) -> Any:
        """Build the §8.5 feedback service over the support repo."""
        try:
            from draftly.feedback import FeedbackService

            return FeedbackService(support_repository=self.dependencies.repositories.support)
        except Exception as exc:
            logger.warning("feedback_service_unavailable: %s", exc)
            return None

    def _build_audit_repo(self) -> Any:
        """Build the §10.2 agent-run audit repository over NeonDB."""
        try:
            from draftly.persistence.repositories.agent_runs import (
                AgentRunsRepository,
            )

            return AgentRunsRepository(self.dependencies.database)
        except Exception as exc:
            logger.warning("audit_repo_unavailable: %s", exc)
            return None

    def _resolve_runtime_model(self) -> Any:
        """Resolve one concrete strands Model for graph agents.

        Returns None in offline mode (no provider keys): graphs then run
        only with injected/deterministic models (tests, CI).
        """
        try:
            from draftly.integrations.strands.models import (
                resolve_concrete_model,
            )

            return resolve_concrete_model(self.dependencies.models.router)
        except Exception as exc:
            logger.warning("runtime_model_unavailable: %s", exc)
            return None

    async def _stop_infrastructure(self) -> None:
        """
        Stop infrastructure services.

        Infrastructure is stopped in reverse dependency order.
        """

        await self._maybe_stop(
            self.dependencies.evaluation,
        )

        # await self._maybe_stop(
        #     self.dependencies.memory,
        # )

        await self._maybe_stop(
            self.dependencies.database,
        )

    # ========================================================
    # Lifecycle Helpers
    # ========================================================

    @staticmethod
    async def _maybe_start(resource: object | None) -> None:
        """
        Call a resource's async or sync start() method when present.
        """

        if resource is None:
            return

        start = getattr(resource, "start", None)

        if start is None:
            return

        result = start()

        if hasattr(result, "__await__"):
            await result

    @staticmethod
    async def _maybe_stop(resource: object | None) -> None:
        """
        Call a resource's async or sync stop()/close() method when present.
        """

        if resource is None:
            return

        stop = getattr(resource, "stop", None)

        if stop is None:
            stop = getattr(resource, "close", None)

        if stop is None:
            return

        result = stop()

        if hasattr(result, "__await__"):
            await result


# ============================================================
# Application Composition
# ============================================================


def create_application(
    *,
    settings: Settings | None = None,
) -> DraftlyApplication:
    """
    Compose the complete Draftly runtime.

    This is the application composition root.

    Dependency construction:

        Settings
           ↓
        Infrastructure
           ↓
        Tools
           ↓
        Agents (deferred to startup)
        Workflows (deferred to startup)
        Events (deferred to startup)
    """

    settings = settings if settings is not None else get_settings()

    # --------------------------------------------------------
    # Infrastructure
    # --------------------------------------------------------

    dependencies = build_dependencies(
        settings=settings,
    )

    # --------------------------------------------------------
    # Tools
    # --------------------------------------------------------

    tools = build_tools()

    # --------------------------------------------------------
    # Worker (background jobs)
    # --------------------------------------------------------

    # Task runner and scheduler will be built after workflows in startup
    worker: DraftlyWorker | None = None

    if settings.worker_enabled:
        # Worker will be built in startup after workflows are ready
        pass

    return DraftlyApplication(
        settings=settings,
        dependencies=dependencies,
        tools=tools,
        agents=None,  # built in startup
        workflows=None,  # built in startup
        events=None,  # built in startup
        worker=worker,
    )


# ============================================================
# FastAPI / ASGI Lifespan
# ============================================================


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncIterator[None]:
    """
    FastAPI application lifespan.

    The lifespan owns the complete Draftly runtime lifecycle:

        FastAPI startup
             ↓
        create_application()
             ↓
        application.startup()
             ↓
        yield
             ↓
        application.shutdown()
             ↓
        FastAPI shutdown

    The composed DraftlyApplication is stored on
    app.state.draftly so API routes, workers, and other
    application components can access the runtime.
    """

    settings = get_settings()

    application = create_application(
        settings=settings,
    )

    app.state.draftly = application

    try:
        await application.startup()

        yield

    finally:
        await application.shutdown()

        # Remove the runtime reference once the application
        # has completely shut down.
        app.state.draftly = None
