from draftly.app.api.evaluation_schemas import T
from sympy.physics.quantum.trace import Tr
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
    #: Where Strands session state lives: "database" persists interrupted
    #: graph state in the shared Postgres/CRDB store (resumable across
    #: restarts/instances); "file" keeps the legacy on-disk per-run sessions.
    session_storage: str = "database"  # "database" | "file"
    max_node_executions: int = 15
    execution_timeout: int = 3600
    node_timeout: int = 1200
    evaluator_max_iterations: int = 2
    review_policy: str = "always"  # "always" | "risky" | "never"

    # ------------------------------------------------------------------
    # Agent steering (spec: 2026-09-10-agent-steering-design)
    # ------------------------------------------------------------------

    #: Master switch for installing steering handlers on application agents.
    steering_enabled: bool = True
    #: Enforce policy actions; when false, steering records decisions and
    #: proceeds (shadow mode).
    steering_enforcement_enabled: bool = True
    #: Version of the role policy matrix applied at runtime.
    steering_policy_version: str = "v1"
    #: Enable the isolated LLM judge to refine deterministic outcomes.
    steering_llm_enabled: bool = True
    #: Automatic tool guides permitted per single tool call.
    steering_tool_guides_per_call: int = 2
    #: Automatic model guides permitted per model turn.
    steering_model_guides_per_turn: int = 2
    #: Total automatic guides permitted per agent invocation.
    steering_total_guides_per_agent: int = 5
    #: Hard timeout for the optional LLM judge call.
    steering_judge_timeout_seconds: float = 10.0
    #: Maximum characters for a steering reason visible in audit/events.
    steering_reason_max_chars: int = 1_000
    #: Maximum serialized bytes for a steering audit/event payload.
    steering_payload_max_bytes: int = 4 * 1024


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
    # Email (SendGrid)
    # ------------------------------------------------------------------

    sendgrid_api_key: str | None = None
    sendgrid_from_email: str = "reviews@draftly.ai"
    sendgrid_from_name: str = "Draftly Reviews"

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
    # "database" = shared Postgres/CRDB session store; "file" = per-run on disk.
    strands_session_storage: str = "database"
    strands_max_node_executions: int = 15
    # A delivered PR docs run executes ~8 sequential LLM nodes; 1200s killed
    # those runs right before the ReviewGate could fire. Leave headroom.
    strands_execution_timeout: int = 3600
    # Slow-network workers see ~50s first-byte on model calls; a 180s per-node
    # budget failed mid-flight nodes. 1200s still bounds a stuck node well
    # under the 3600s run deadline while letting slow runs finish.
    strands_node_timeout: int = 1200
    strands_evaluator_max_iterations: int = 2
    strands_review_policy: str = "always"  # "always" | "risky" | "never"

    # ------------------------------------------------------------------
    # Agent steering (spec: 2026-09-10-agent-steering-design)
    # ------------------------------------------------------------------

    strands_steering_enabled: bool = True
    strands_steering_enforcement_enabled: bool = True
    strands_steering_policy_version: str = "v1"
    strands_steering_llm_enabled: bool = True
    strands_steering_tool_guides_per_call: int = 2
    strands_steering_model_guides_per_turn: int = 2
    strands_steering_total_guides_per_agent: int = 5
    strands_steering_judge_timeout_seconds: float = 10.0
    strands_steering_reason_max_chars: int = 1_000
    strands_steering_payload_max_bytes: int = 4 * 1024

    @property
    def strands(self) -> StrandsConfig:
        """Return the Strands runtime config derived from settings."""
        return StrandsConfig(
            graph_id=self.strands_graph_id,
            session_storage_dir=self.strands_session_storage_dir,
            session_storage=self.strands_session_storage,
            max_node_executions=self.strands_max_node_executions,
            execution_timeout=self.strands_execution_timeout,
            node_timeout=self.strands_node_timeout,
            evaluator_max_iterations=self.strands_evaluator_max_iterations,
            review_policy=self.strands_review_policy,
            steering_enabled=self.strands_steering_enabled,
            steering_enforcement_enabled=self.strands_steering_enforcement_enabled,
            steering_policy_version=self.strands_steering_policy_version,
            steering_llm_enabled=self.strands_steering_llm_enabled,
            steering_tool_guides_per_call=self.strands_steering_tool_guides_per_call,
            steering_model_guides_per_turn=self.strands_steering_model_guides_per_turn,
            steering_total_guides_per_agent=self.strands_steering_total_guides_per_agent,
            steering_judge_timeout_seconds=self.strands_steering_judge_timeout_seconds,
            steering_reason_max_chars=self.strands_steering_reason_max_chars,
            steering_payload_max_bytes=self.strands_steering_payload_max_bytes,
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
