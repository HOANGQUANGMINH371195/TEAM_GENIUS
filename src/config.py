from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # LLM / embeddings are intentionally provider-neutral until local runtime is chosen.
    llm_provider: str = ""
    model_name: str = ""
    openai_api_key: str = ""
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimensions: int | None = Field(default=None, ge=1)

    # Supabase PostgreSQL
    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_pool_recycle: int = Field(default=1800, ge=60)

    # GraphRAG
    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    graph_hops: int = Field(default=1, ge=0, le=5)
    graph_neighbor_limit: int = Field(default=20, ge=1, le=100)
    chunk_size: int = Field(default=800, ge=100)
    chunk_overlap: int = Field(default=120, ge=0)

    # Optional telemetry
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    @property
    def embeddings_configured(self) -> bool:
        return bool(self.embedding_provider and self.embedding_model)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_provider and self.model_name)

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    def validate_chunk_settings(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    # Deprecated: retained only so older imports fail safely without Chroma runtime.
    chroma_persist_dir: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
