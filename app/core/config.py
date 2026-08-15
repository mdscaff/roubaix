from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # LLM providers (Roubaix synthesis; bridged to Cognee in cognee_setup.py)
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # Cognee Cloud HTTP API — not used for local SDK + pgGraph
    cognee_api_key: str | None = Field(default=None, alias="COGNEE_API_KEY")
    cognee_base_url: str | None = Field(default=None, alias="COGNEE_BASE_URL")

    default_model: str = Field(default="openai/gpt-4o-mini", alias="DEFAULT_MODEL")
    default_llm_endpoint: str | None = Field(
        default="https://openrouter.ai/api/v1",
        alias="DEFAULT_LLM_ENDPOINT",
    )
    default_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="DEFAULT_EMBEDDING_MODEL",
    )
    default_embedding_dimensions: int = Field(default=1536, alias="DEFAULT_EMBEDDING_DIMENSIONS")

    cognee_setup_enabled: bool = Field(default=True, alias="ROUBAIX_COGNEE_SETUP")
    graph_database_provider: str | None = Field(default=None, alias="GRAPH_DATABASE_PROVIDER")

    default_dataset: str = Field(default="default", alias="ROUBAIX_DEFAULT_DATASET")
    max_evidence_items: int = Field(default=12, alias="ROUBAIX_MAX_EVIDENCE_ITEMS")
    max_retries: int = Field(default=2, alias="ROUBAIX_MAX_RETRIES")
    enable_temporal: bool = Field(default=True, alias="ROUBAIX_ENABLE_TEMPORAL")

    # Evidence packing. The token budget is the real cost lever: item count
    # alone does not bound prompt size when items vary from 20 to 2000 chars.
    evidence_token_budget: int = Field(default=1200, alias="ROUBAIX_EVIDENCE_TOKEN_BUDGET")

    # Timeouts. An agent harness without them converts a slow dependency into
    # an unbounded request.
    retrieval_timeout_s: float = Field(default=20.0, alias="ROUBAIX_RETRIEVAL_TIMEOUT_S")
    synthesis_timeout_s: float = Field(default=60.0, alias="ROUBAIX_SYNTHESIS_TIMEOUT_S")

    # When false (the default), the runtime controller fails closed rather than
    # answering from stub/fallback evidence. CI sets this true so the suite can
    # exercise the pipeline without a live Cognee instance.
    allow_stub_evidence: bool = Field(default=False, alias="ROUBAIX_ALLOW_STUB_EVIDENCE")

    # When true, a freshness-required query must return evidence carrying a
    # parseable date. Without this the freshness contract is satisfied by any
    # non-empty result, which is how stale answers pass a freshness gate.
    strict_freshness: bool = Field(default=True, alias="ROUBAIX_STRICT_FRESHNESS")

    # Cache settings (inspired by HyperSpace Content Store)
    cache_max_size: int = Field(default=4096, alias="ROUBAIX_CACHE_MAX_SIZE")
    cache_default_ttl_s: float = Field(default=3600.0, alias="ROUBAIX_CACHE_DEFAULT_TTL_S")
    cache_freshness_ttl_s: float = Field(default=120.0, alias="ROUBAIX_CACHE_FRESHNESS_TTL_S")

    # Temporal settings
    temporal_host: str = Field(default="localhost:7233", alias="TEMPORAL_HOST")
    temporal_namespace: str = Field(default="default", alias="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field(default="roubaix-router", alias="TEMPORAL_TASK_QUEUE")

    elevenlabs_api_key: str | None = Field(default=None, alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str | None = Field(default=None, alias="ELEVENLABS_VOICE_ID")


settings = Settings()
