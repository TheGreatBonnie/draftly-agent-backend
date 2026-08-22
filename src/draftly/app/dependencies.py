# app/dependencies.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from draftly.app.config import Settings
from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.document_store import DocumentStore
from draftly.integrations.database.evaluations_store import DatabaseEvaluationsStore
from draftly.integrations.database.jobs_store import DatabaseJobsStore
from draftly.integrations.database.memory_store import DatabaseMemoryStore
from draftly.integrations.database.vector_search import VectorSearch
from draftly.integrations.discord.auth import DiscordAuth
from draftly.integrations.discord.client import DiscordClient
from draftly.integrations.discord.gateway import DiscordGateway
from draftly.integrations.github.auth import GitHubAuth
from draftly.integrations.github.client import GitHubClient
from draftly.integrations.slack.app import build_slack_app
from draftly.integrations.slack.auth import SlackAuth
from draftly.integrations.slack.client import SlackClient
from draftly.integrations.slack.installation_store import SlackInstallationStore

# from draftly.memory.embeddings import build_memory_embedder
# from draftly.memory.manager import MemoryManager
from draftly.persistence.repositories.delivery import DeliveryRepository
from draftly.persistence.repositories.documents import DocumentRepository
from draftly.persistence.repositories.evaluations import EvaluationRepository
from draftly.persistence.repositories.events import EventRepository
from draftly.persistence.repositories.jobs import JobRepositoryImpl
from draftly.persistence.repositories.memory import MemoryRepository
from draftly.persistence.repositories.reviewers import ReviewersRepository
from draftly.persistence.repositories.reviews import ReviewsRepository
from draftly.persistence.repositories.support import SupportRepository

logger = logging.getLogger(__name__)

# ============================================================
# Model Dependencies
# ============================================================


@dataclass(slots=True)
class ModelDependencies:
    """
    LLM/model dependencies used by Draftly agents.

    ``fast`` and ``reasoning`` are logical model handles resolved
    from the ModelRouter; agents never construct provider models.
    """

    fast: Any
    reasoning: Any
    research: Any
    review: Any
    rubric_grader: Any
    router: Any
    max_output_tokens: dict[str, int] | None = None


def build_models(
    settings: Settings,
) -> ModelDependencies:
    """
    Build Draftly's models through the model runtime.

    The router picks the best healthy provider/model per capability,
    with cross-provider fallback. Fails fast (``RuntimeError``) if no
    provider is configured at all.
    """

    from draftly.models.factory import build_model_router
    from draftly.models.policies import RoutingPolicy

    router = build_model_router()

    fast = router.resolve(
        RoutingPolicy(
            required_capabilities=("tool_calling",),
            allow_fallback=True,
        )
    )

    reasoning = router.resolve(
        RoutingPolicy(
            required_capabilities=("reasoning", "tool_calling"),
            allow_fallback=True,
        )
    )

    research = router.resolve_model("stage-research")
    review = router.resolve_model("stage-review")
    rubric_grader = router.resolve_model("stage-rubric-grader")

    from draftly.models.factory import build_agent_policies

    policies = build_agent_policies()
    max_output_tokens = {role: policy.max_output_tokens for role, policy in policies.items()}

    logger.info(
        "resolved model handles fast=%s reasoning=%s research=%s review=%s rubric_grader=%s",
        getattr(fast, "model", "?") or "?",
        getattr(reasoning, "model", "?") or "?",
        getattr(research, "model", "?") or "?",
        getattr(review, "model", "?") or "?",
        getattr(rubric_grader, "model", "?") or "?",
    )

    return ModelDependencies(
        fast=fast,
        reasoning=reasoning,
        research=research,
        review=review,
        rubric_grader=rubric_grader,
        router=router,
        max_output_tokens=max_output_tokens,
    )


# ============================================================
# Integration Dependencies
# ============================================================


@dataclass(slots=True)
class IntegrationDependencies:
    """
    External service clients used by Draftly.

    Integrations encapsulate provider-specific APIs.
    """

    database: DatabaseClient
    github: GitHubClient
    slack: SlackClient
    discord: DiscordClient
    evaluation_client: Any = None
    slack_app: Any | None = None
    discord_gateway: Any | None = None


def build_integrations(
    settings: Settings,
) -> IntegrationDependencies:
    """
    Construct Draftly's external service integrations.
    """

    database = DatabaseClient(
        database_url=settings.database_url,
    )

    github = GitHubClient(
        auth=(GitHubAuth(token=settings.github_token) if settings.github_token else None),
        repository=settings.github_repository,
    )

    slack = SlackClient(
        auth=(SlackAuth(token=settings.slack_bot_token) if settings.slack_bot_token else None),
    )

    discord = DiscordClient(
        auth=(
            DiscordAuth(token=settings.discord_bot_token) if settings.discord_bot_token else None
        ),
    )

    # Evaluation client (Strands Evals) is wired in Phase 7 (§8.2).
    evaluation_client = None

    # Build Slack Bolt app if tokens are present
    slack_app = None
    if settings.slack_bot_token and settings.slack_signing_secret:
        slack_app = build_slack_app(
            signing_secret=settings.slack_signing_secret,
            installation_store=SlackInstallationStore(
                DatabaseClient(database_url=settings.database_url),
            ),
        )

    # Build Discord gateway if token is present
    discord_gateway = None
    if settings.discord_bot_token:
        discord_gateway = DiscordGateway()

    return IntegrationDependencies(
        database=database,
        github=github,
        slack=slack,
        discord=discord,
        evaluation_client=evaluation_client,
        slack_app=slack_app,
        discord_gateway=discord_gateway,
    )


# ============================================================
# Repository Dependencies
# ============================================================


@dataclass(slots=True)
class RepositoryDependencies:
    """
    Persistence repositories used by domain services and
    workflows.

    Repositories depend on infrastructure clients but do not
    know about agents or tools.
    """

    delivery: DeliveryRepository
    events: EventRepository
    memory: MemoryRepository
    documents: DocumentRepository
    evaluations: EvaluationRepository
    support: SupportRepository
    jobs: JobRepositoryImpl
    reviews: ReviewsRepository
    reviewers: ReviewersRepository


def build_repositories(
    database: DatabaseClient,
) -> RepositoryDependencies:
    """
    Construct all Draftly persistence repositories.
    """

    delivery = DeliveryRepository(
        client=database,
    )

    events = EventRepository(
        database=database,
    )

    memory = MemoryRepository(
        store=DatabaseMemoryStore(
            client=database,
        ),
        vector_search=VectorSearch(
            client=database,
        ),
    )

    documents = DocumentRepository(
        store=DocumentStore(
            client=database,
        ),
    )

    evaluations = EvaluationRepository(
        store=DatabaseEvaluationsStore(
            client=database,
        ),
    )

    support = SupportRepository(
        database=database,
    )

    jobs = JobRepositoryImpl(
        store=DatabaseJobsStore(
            client=database,
        ),
    )

    reviews = ReviewsRepository(
        database=database,
    )

    reviewers = ReviewersRepository(
        database=database,
    )

    return RepositoryDependencies(
        delivery=delivery,
        events=events,
        memory=memory,
        documents=documents,
        evaluations=evaluations,
        support=support,
        jobs=jobs,
        reviews=reviews,
        reviewers=reviewers,
    )


# ============================================================
# Memory Dependencies
# ============================================================


# def build_memory(
#     *,
#     repository: MemoryRepository,
#     database: DatabaseClient | None = None,
# ) -> MemoryManager:
#     """
#     Construct Draftly's agentic memory subsystem.

#     The memory manager sits above persistence and the
#     NeonDB integration layer.

#     Dependency direction:

#         tools/memory/
#              ↓
#         memory/manager.py
#              ↓
#         persistence/repositories/memory.py
#              ↓
#         integrations/database/
#     """

#     del database  # noqa: ARG001 — injected for interface stability only

#     embedder = build_memory_embedder()

#     return MemoryManager(
#         repository=repository,
#         embedder=embedder,
#     )


# ============================================================
# Evaluation Dependencies
# ============================================================


@dataclass(slots=True)
class EvaluationDependencies:
    """
    Evaluation dependencies used by Draftly's
    evaluation workflows and evaluation tools.

    ``client`` is the Strands Evals integration, wired in Phase 7 (§8.2).
    """

    repository: EvaluationRepository
    client: Any = None


def build_evaluation(
    *,
    client: Any = None,
    repository: EvaluationRepository,
) -> EvaluationDependencies:
    """
    Construct the evaluation subsystem.
    """

    return EvaluationDependencies(
        client=client,
        repository=repository,
    )


# ============================================================
# Complete Application Dependencies
# ============================================================


@dataclass(slots=True)
class ApplicationDependencies:
    """
    All infrastructure dependencies required by Draftly.

    This object is passed into the composition layer.

    It intentionally contains infrastructure and shared runtime
    services—not agents, tools, or workflows.
    """

    settings: Settings

    models: ModelDependencies

    integrations: IntegrationDependencies

    repositories: RepositoryDependencies

    # memory: MemoryManager

    evaluation: EvaluationDependencies

    @property
    def database(self) -> DatabaseClient:
        """
        Convenience access to the NeonDB integration.
        """

        return self.integrations.database

    @property
    def github(self) -> GitHubClient:
        """
        Convenience access to GitHub integration.
        """

        return self.integrations.github

    @property
    def slack(self) -> SlackClient:
        """
        Convenience access to Slack integration.
        """

        return self.integrations.slack

    @property
    def discord(self) -> DiscordClient:
        """
        Convenience access to Discord integration.
        """

        return self.integrations.discord

    @property
    def evaluation_client(self) -> Any:
        """
        Convenience access to the Strands Evals integration
        (wired in Phase 7).
        """

        return self.integrations.evaluation_client


# ============================================================
# Dependency Factory
# ============================================================


def build_dependencies(
    *,
    settings: Settings,
) -> ApplicationDependencies:
    """
    Build Draftly's complete infrastructure dependency graph.

    Dependency construction order:

        Settings
           ↓
        Models
           ↓
        Integrations
           ↓
        Repositories
           ↓
        Memory
           ↓
        Evaluation
           ↓
        ApplicationDependencies
    """

    # --------------------------------------------------------
    # 1. Models
    # --------------------------------------------------------

    models = build_models(
        settings=settings,
    )

    # --------------------------------------------------------
    # 2. External integrations
    # --------------------------------------------------------

    integrations = build_integrations(
        settings=settings,
    )

    # --------------------------------------------------------
    # 3. Persistence repositories
    # --------------------------------------------------------

    repositories = build_repositories(
        database=integrations.database,
    )

    # --------------------------------------------------------
    # 4. Agentic memory
    # --------------------------------------------------------

    # memory = build_memory(
    #     repository=repositories.memory,
    #     database=integrations.database,
    # )

    # --------------------------------------------------------
    # 5. Evaluation
    # --------------------------------------------------------

    evaluation = build_evaluation(
        client=integrations.evaluation_client,
        repository=repositories.evaluations,
    )

    # --------------------------------------------------------
    # 6. Return complete dependency graph
    # --------------------------------------------------------

    return ApplicationDependencies(
        settings=settings,
        models=models,
        integrations=integrations,
        repositories=repositories,
        # memory=memory,
        evaluation=evaluation,
    )
