
from pydantic import AliasChoices, Field  # ty: ignore[unresolved-import]
from pydantic_settings import BaseSettings, SettingsConfigDict  # ty: ignore[unresolved-import]


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
    # CockroachDB
    # ------------------------------------------------------------------

    database_url: str = Field(
        default="postgresql://localhost:26257/draftly",
        validation_alias=AliasChoices("COCKROACHDB_URL", "DATABASE_URL"),
    )

    database_pool_min_size: int = 2
    database_pool_max_size: int = 10

    # ------------------------------------------------------------------
    # DeepEval
    # ------------------------------------------------------------------

    deepeval_api_key: str | None = None

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
