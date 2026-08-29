from __future__ import annotations

import json
import os
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

    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"
    max_request_body_bytes: int = Field(default=131_072, ge=1_024, le=4_194_304)
    rate_limit_requests: int = Field(default=60, ge=1, le=10_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_redis_url: str = ""
    conversation_cache_ttl_seconds: int = Field(default=120, ge=10, le=3600)
    conversation_cache_max_turns: int = Field(default=10, ge=1, le=20)
    cost_quota_units: int = Field(default=100_000, ge=1_000, le=10_000_000)
    cost_quota_window_seconds: int = Field(default=86_400, ge=60, le=31_536_000)
    metrics_token: str = ""

    llm_provider: str = "openai"
    model_name: str = ""
    openai_api_key: str = ""
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_output_tokens: int = Field(default=900, ge=64, le=4_096)
    # Luna's grounded answer contract does not need hidden chain-of-thought
    # for every request; disabling extra reasoning materially reduces TTFT
    # while retrieval/verifier guardrails remain unchanged.
    llm_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "none"
    llm_verbosity: Literal["low", "medium", "high"] = "low"
    llm_use_responses_api: bool = True
    query_rewrite_max_tokens: int = Field(default=180, ge=64, le=512)
    query_rewrite_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    model_router_enabled: bool = True
    model_router_model_name: str = "gpt-5.6-luna"
    model_router_timeout_seconds: float = Field(default=4.0, gt=0.1, le=10.0)
    model_router_max_tokens: int = Field(default=160, ge=64, le=512)
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1)
    embedding_api_key: str = ""

    database_url: str = ""
    # Explicit runtime alias for managed deployments. DATABASE_URL remains a
    # compatibility alias for local/dev tooling.
    runtime_database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_connect_timeout: int = Field(default=10, ge=1)
    db_pool_recycle: int = Field(default=1800, ge=60)

    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "medical_legal_active"
    qdrant_timeout_seconds: float = Field(default=30.0, gt=0)
    # Supabase pool checkout plus the bounded original/HyDE retrieval cascade
    # can legitimately exceed 15s on a cold/free-tier connection.  A timeout
    # shorter than the measured staging path turns a recoverable slow request
    # into an agent_error before answer generation starts.
    # Legal-unit expansion may require two bounded PostgreSQL passes on the
    # free-tier pool; allow the verified request to finish instead of turning
    # a slow cold connection into an agent error.
    retrieval_timeout_seconds: float = Field(default=45.0, gt=0)
    provider_concurrency: int = Field(default=8, ge=1, le=64)
    provider_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    provider_circuit_cooldown_seconds: float = Field(default=30.0, ge=1, le=600)

    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    # Candidate pool is intentionally wider than the final evidence pack: a
    # lexical/coverage re-ranker needs enough semantic candidates to recover
    # an operative clause from a broad document-level embedding match.
    retrieval_candidate_k: int = Field(default=60, ge=10, le=200)
    semantic_similarity_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    query_rewrite_enabled: bool = False
    graph_hops: int = Field(default=1, ge=0, le=5)
    graph_neighbor_limit: int = Field(default=20, ge=1, le=100)
    graph_evidence_limit: int = Field(default=10, ge=1, le=100)
    max_llm_evidence: int = Field(default=12, ge=1, le=100)
    max_citations: int = Field(default=12, ge=1, le=50)
    max_chunks_per_document: int = Field(default=4, ge=1, le=20)
    max_context_chars: int = Field(default=100_000, ge=1_000, le=200_000)
    max_context_tokens: int = Field(default=32_000, ge=512, le=64_000)
    chunk_size: int = Field(default=800, ge=100)
    chunk_overlap: int = Field(default=120, ge=0)
    # Optional local cross-encoder; the default heuristic path has no model
    # download and is used unless an explicit ablation enables this backend.
    reranker_backend: Literal["heuristic", "cross_encoder"] = "heuristic"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_max_candidates: int = Field(default=30, ge=1, le=64)
    community_index_path: str = ""
    global_max_communities: int = Field(default=3, ge=1, le=12)
    global_max_rounds: int = Field(default=2, ge=1, le=3)
    experience_index_path: str = ""
    research_queue_backend: Literal["memory", "redis"] = "memory"
    research_queue_redis_url: str = ""
    research_queue_max_pending: int = Field(default=32, ge=1, le=10_000)
    research_queue_max_workers: int = Field(default=2, ge=1, le=64)
    research_queue_timeout_seconds: float = Field(default=90.0, gt=0, le=3600)
    research_queue_ttl_seconds: int = Field(default=900, ge=60, le=86_400)

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    langfuse_base_url: str = ""
    # Langfuse Prompt Registry lineage.  The local prompt remains a safe
    # fallback for tests/offline operation; production records the selected
    # registry version in every generation trace.
    prompt_registry_name: str = "medipay-system"
    prompt_registry_label: str = "production"
    prompt_registry_cache_ttl_seconds: int = Field(default=300, ge=30, le=86_400)
    # Prompt lookup is control-plane I/O and must not consume the chat
    # generation budget when a production label is missing or unavailable.
    langfuse_timeout_seconds: int = Field(default=2, ge=1, le=30)

    # Optional OTLP/HTTP exporter. Empty endpoint deliberately keeps local
    # development dependency-free while production can point at Langfuse or a
    # self-hosted collector without changing the request path.
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""
    otel_service_name: str = "medipay-api"
    otel_sampling_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    otel_max_queue_size: int = Field(default=2048, ge=128, le=20_000)
    otel_max_export_batch_size: int = Field(default=256, ge=32, le=2_048)
    otel_schedule_delay_millis: int = Field(default=5_000, ge=100, le=60_000)
    otel_export_timeout_seconds: float = Field(default=5.0, gt=0.1, le=30.0)

    # Explicit kill switches for staged rollout and rollback.
    feature_planner_enabled: bool = True
    feature_reranker_enabled: bool = True
    feature_auditor_enabled: bool = True
    feature_calculator_enabled: bool = True
    feature_viewer_enabled: bool = True
    feature_timeline_enabled: bool = True
    feature_eligibility_enabled: bool = True
    feature_graph_enabled: bool = True
    feature_global_search_enabled: bool = False
    feature_experience_retrieval_enabled: bool = False

    # Firebase Admin SDK
    firebase_service_account_json: str = ""

    @property
    def embeddings_configured(self) -> bool:
        return bool(self.embedding_provider and self.embedding_model and self.openai_api_key)

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider.casefold() == "openai" and bool(
            self.model_name and self.openai_api_key
        )

    @property
    def database_configured(self) -> bool:
        return bool(self.effective_database_url)

    @property
    def effective_database_url(self) -> str:
        return self.runtime_database_url or self.database_url

    @property
    def langfuse_configured(self) -> bool:
        return bool(
            self.langfuse_public_key
            and self.langfuse_secret_key
            and (self.langfuse_base_url or self.langfuse_host)
        )

    def validate_chunk_settings(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    def validate_production_contract(self) -> None:
        """Fail closed before traffic if a managed deployment is incomplete."""
        if self.app_env != "production":
            return
        missing: list[str] = []
        required_values = {
            "RUNTIME_DATABASE_URL/DATABASE_URL": self.effective_database_url,
            "QDRANT_URL": self.qdrant_url,
            "QDRANT_API_KEY": self.qdrant_api_key,
            "NEO4J_URI": self.neo4j_uri,
            "NEO4J_PASSWORD": self.neo4j_password,
            "OPENAI_API_KEY": self.openai_api_key,
            "MODEL_NAME": self.model_name,
            "METRICS_TOKEN": self.metrics_token,
        }
        missing.extend(name for name, value in required_values.items() if not str(value).strip())
        firebase_json = self.firebase_service_account_json.strip()
        if not firebase_json and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            missing.append("FIREBASE_SERVICE_ACCOUNT_JSON/GOOGLE_APPLICATION_CREDENTIALS")
        elif firebase_json:
            try:
                service_account = json.loads(firebase_json)
            except json.JSONDecodeError:
                service_account = None
            if not isinstance(service_account, dict) or not all(
                str(service_account.get(field, "")).strip()
                for field in ("type", "project_id", "client_email", "private_key")
            ):
                missing.append("FIREBASE_SERVICE_ACCOUNT_JSON (valid service-account JSON)")
        origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        if not origins or "*" in origins or any("localhost" in origin for origin in origins):
            missing.append("CORS_ORIGINS (explicit HTTPS origins)")
        if self.feature_global_search_enabled:
            if not self.community_index_path.strip():
                missing.append("COMMUNITY_INDEX_PATH when FEATURE_GLOBAL_SEARCH_ENABLED=true")
            if self.research_queue_backend != "redis":
                missing.append("RESEARCH_QUEUE_BACKEND=redis when FEATURE_GLOBAL_SEARCH_ENABLED=true")
            if not self.research_queue_redis_url.strip():
                missing.append("RESEARCH_QUEUE_REDIS_URL when FEATURE_GLOBAL_SEARCH_ENABLED=true")
        if self.feature_experience_retrieval_enabled and not self.experience_index_path.strip():
            missing.append("EXPERIENCE_INDEX_PATH when FEATURE_EXPERIENCE_RETRIEVAL_ENABLED=true")
        if missing:
            raise ValueError("Production configuration incomplete: " + ", ".join(missing))


@lru_cache
def get_settings() -> Settings:
    return Settings()
