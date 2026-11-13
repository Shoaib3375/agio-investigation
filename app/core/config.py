"""Application settings loaded from environment via pydantic-settings.

RabbitMQ, the Laravel internal API token, and the LLM credentials are all
optional so the service can start without RabbitMQ or an LLM. The
investigation database URL is the only required value.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed settings sourced from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application --------------------------------------------------------
    app_env: str = "local"
    log_level: str = "INFO"

    # Investigation database (REQUIRED) ---------------------------------
    # Async SQLAlchemy via asyncpg. Separate from Laravel's database.
    investigation_database_url: str

    # RabbitMQ (optional — wired now, consumed in PY-M02) ----------------
    rabbitmq_url: str | None = None
    rabbitmq_exchange: str = "agio.events"
    rabbitmq_queue: str = "agio.investigation"
    rabbitmq_queue_durable: bool = True
    rabbitmq_routing_key: str = "#"
    rabbitmq_dlx: str = "agio.dlx"
    rabbitmq_dlq: str = "agio.dead_letters"
    rabbitmq_retry_exchange: str = "agio.retry"
    rabbitmq_retry_queue: str = "agio.retry.60s"
    rabbitmq_retry_ttl_ms: int = 60000
    rabbitmq_prefetch: int = 16
    rabbitmq_max_retries: int = 3

    # Laravel internal investigation API (optional — wired, used in PY-M03)
    laravel_internal_api_url: str | None = None
    laravel_service_token: str | None = None
    laravel_api_timeout_seconds: int = 10
    laravel_api_max_retries: int = 2

    # Admin API authentication (PY-M06)
    # A static bearer token for the admin-facing API.
    # In production, replace with a proper JWT / RBAC mechanism.
    admin_api_key: str | None = None

    # LLM (PY-M07) ──────────────────────────────────────────────────────
    llm_provider: str = "disabled"       # "disabled" | "openai"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 2048
    llm_prompt_version: str = "1.0.0"

    # Retention (PY-M09) ─────────────────────────────────────────────────
    retention_event_inbox_days: int = 30     # delete processed events after N days
    retention_investigation_days: int = 90   # delete completed investigations after N days
    retention_interval_seconds: int = 21600  # run cleanup every 6 hours


settings = Settings()
