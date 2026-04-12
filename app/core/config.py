from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    cognee_api_key: str | None = Field(default=None, alias="COGNEE_API_KEY")
    cognee_base_url: str | None = Field(default=None, alias="COGNEE_BASE_URL")

    default_model: str = Field(default="gpt-5", alias="DEFAULT_MODEL")
    default_embedding_model: str = Field(
        default="text-embedding-3-large", alias="DEFAULT_EMBEDDING_MODEL"
    )

    default_dataset: str = Field(default="default", alias="ROUBAIX_DEFAULT_DATASET")
    max_evidence_items: int = Field(default=12, alias="ROUBAIX_MAX_EVIDENCE_ITEMS")
    max_retries: int = Field(default=2, alias="ROUBAIX_MAX_RETRIES")
    enable_temporal: bool = Field(default=True, alias="ROUBAIX_ENABLE_TEMPORAL")


settings = Settings()
