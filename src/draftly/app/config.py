from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrandsConfig(BaseModel):
    """
    Strands agents runtime configuration.

    ``review_policy`` controls when the human-in-the-loop review node
    gates a run: ``"always"``, ``"risky"`` (only high-risk payloads), or
    ``"never"``.
    """

    graph_id: str = "draftly-main-graph"
    session_storage_dir: str = ".draftly/sessions"
    max_node_executions: int = 10
    execution_timeout: int = 600
    node_timeout: int = 180
    review_policy: str = "always"  # "always" | "risky" | "never"


class Settings(BaseSettings):
    """
    Draftly application configuration.

    Values are loaded from environment variables and .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    app_name: str = "Draftly"
    environment: str = "development"
    debug: bool = False
    version: str = "0.1.0"

    host: str = "0.0.0.0"
    port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    api_prefix: str = "/api"

    api_key: str | None = None

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    fast_model: str = "gpt-4.1-mini"
    reasoning_model: str = "gpt-4.1"

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------

    github_token: str | None = None
    github_webhook_secret: str | None = None
    github_repository: str | None = None
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_private_key_path: str | None = None

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_app_token: str | None = None
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_redirect_uri: str | None = None
    slack_scopes: list[str] | None = None

    # ------------------------------------------------------------------
    # Discord
    # ------------------------------------------------------------------

    discord_bot_token: str | None = None
    discord_public_key: str | None = None
    discord_app_id: str | None = None
    discord_guild_id: str | None = None

    # ------------------------------------------------------------------
    # Database (NeonDB / Postgres)
    # ------------------------------------------------------------------

    database_url: str = Field(
        default="postgresql://localhost:5432/draftly",
        validation_alias=AliasChoices(
            "NEON_DATABASE_URL",
            "DATABASE_URL",
        ),
    )

    database_pool_min_size: int = 2
    database_pool_max_size: int = 10

    # ------------------------------------------------------------------
    # Strands (agents runtime)
    # ------------------------------------------------------------------

    strands_graph_id: str = "draftly-main-graph"
    strands_session_storage_dir: str = ".draftly/sessions"
    strands_max_node_executions: int = 10
    strands_execution_timeout: int = 600
    strands_node_timeout: int = 180
    strands_review_policy: str = "always"  # "always" | "risky" | "never"

    @property
    def strands(self) -> StrandsConfig:
        """Return the Strands runtime config derived from settings."""
        return StrandsConfig(
            graph_id=self.strands_graph_id,
            session_storage_dir=self.strands_session_storage_dir,
            max_node_executions=self.strands_max_node_executions,
            execution_timeout=self.strands_execution_timeout,
            node_timeout=self.strands_node_timeout,
            review_policy=self.strands_review_policy,
        )

    # ------------------------------------------------------------------
    # Events streaming (spec: 2026-08-23-event-streaming-design)
    # ------------------------------------------------------------------

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL"),
    )
    events_streaming_enabled: bool = True
    events_heartbeat_seconds: int = 15

    # ------------------------------------------------------------------
    # Redis subsystems
    # ------------------------------------------------------------------

    semantic_cache_enabled: bool = True
    semantic_cache_similarity_threshold: float = 0.90
    vector_search_backend: str = "dual"  # "redis" | "pgvector" | "dual"
    event_bus_backend: str = "dual"  # "pubsub" | "stream" | "dual"
    rate_limiting_enabled: bool = True
    api_cache_enabled: bool = True

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    worker_enabled: bool = True
    worker_concurrency: int = 4

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    scheduler_enabled: bool = True

    # ------------------------------------------------------------------
    # Redis Queue (RQ)
    # ------------------------------------------------------------------

    rq_queue_prefix: str = "draftly"
    rq_scheduler_enabled: bool = True
    rq_worker_queues: list[str] = ["scheduled", "webhooks", "default"]
    # When enabled, POST /onboarding/initialize dispatches to the RQ worker
    # (default queue) and returns immediately; the worker process releases
    # the init lock. Defaults to on so job dispatch goes through the RQ queue,
    # with in-process execution only used when no RQ queues are wired.
    rq_enabled: bool = True

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    require_api_key: bool = False
    clerk_publishable_key: str | None = None
    clerk_secret_key: str | None = None
    clerk_signing_secret: str | None = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Token Cost Management
    # ------------------------------------------------------------------

    max_tokens_per_run: int = 50000  # soft budget per agent run
    summarization_trigger_fraction: float = 0.70  # trigger at 70% context
    recursion_limit: int = 30  # max graph steps per subagent invocation


def get_settings() -> Settings:
    """
    Return the application settings.
    """
    return Settings()
