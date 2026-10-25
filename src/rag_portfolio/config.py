from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    tenant_id: str = Field(default="portfolio", min_length=1, max_length=100)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5545, ge=1, le=65535)
    postgres_database: str = "rag_portfolio"
    postgres_user: str = "rag_portfolio"
    postgres_password: SecretStr = SecretStr("")
    qdrant_url: str = "http://127.0.0.1:6534"
    qdrant_api_key: SecretStr = SecretStr("")
    embedding_provider: str = "alibaba_dashscope"
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_output_type: str = "dense"
    embedding_timeout_seconds: float = Field(default=30, gt=0)
    service_token: SecretStr = SecretStr("")
    admin_token: SecretStr = SecretStr("")
    agent_token: SecretStr = SecretStr("")
    agent_scope_keys: list[str] = Field(default_factory=lambda: ["shared"], min_length=1)
    agent_audience: Literal["patient", "doctor", "internal"] = "internal"
    read_scope_keys: list[str] = Field(default_factory=lambda: ["shared"], min_length=1)
    # Business routing belongs to the knowledge service, not its Agent consumers.
    knowledge_global_kb_ids: list[UUID] = Field(default_factory=list, max_length=6)
    knowledge_space_kb_ids: dict[
        str,
        dict[Literal["help", "product"], Annotated[list[UUID], Field(min_length=1, max_length=6)]],
    ] = Field(default_factory=dict)
    knowledge_strategy: Literal["dense", "sparse", "hybrid"] = "hybrid"
    knowledge_candidate_k: int = Field(default=40, ge=1, le=200)
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    knowledge_max_context_tokens: int = Field(default=4000, ge=128, le=16000)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    build_revision: str = "development"
    worker_heartbeat_max_age: float = Field(default=30, ge=3, le=300)
    dependency_timeout_seconds: float = Field(default=3, ge=0.1, le=30)
    asset_root: str = "data"
    ingestion_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    ingestion_chunk_size: int = Field(default=512, ge=128, le=4096)
    ingestion_chunk_overlap: int = Field(default=64, ge=0, le=127)
    docling_ocr: bool = False
    docling_tables: bool = True
    docling_threads: int = Field(default=2, ge=1, le=16)
    docling_artifacts_path: str = ""
    docling_model_revision: str = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
    rerank_model: str = "qwen3.7-text-rerank"
    answer_base_url: str = ""
    answer_api_key: SecretStr = SecretStr("")
    answer_model: str = ""
    demo_retrieval_enabled: bool = False
    demo_token: SecretStr = SecretStr("")
    demo_tenant_id: str = "integration-demo"
    demo_scope_key: str = "integration-demo"
    demo_corpus_path: str = ""
    demo_corpus_sha256: str = ""

    @model_validator(mode="after")
    def validate_credentials(self) -> "Settings":
        if self.demo_retrieval_enabled:
            if self.environment == "production":
                raise ValueError("Demo retrieval is prohibited in production")
            demo = self.demo_token.get_secret_value()
            if len(demo) < 32 or demo in {
                self.service_token.get_secret_value(),
                self.admin_token.get_secret_value(),
            }:
                raise ValueError("Demo requires a distinct token of at least 32 characters")
            if not self.demo_corpus_path or len(self.demo_corpus_sha256) != 64:
                raise ValueError("Demo requires an explicit corpus path and SHA256 pin")
        tokens = [self.service_token.get_secret_value(), self.admin_token.get_secret_value()]
        if any(token and len(token) < 32 for token in tokens):
            raise ValueError("Service tokens must contain at least 32 characters")
        if tokens[0] and tokens[0] == tokens[1]:
            raise ValueError("Read and admin tokens must be different")
        agent = self.agent_token.get_secret_value()
        if agent and (len(agent) < 32 or agent in tokens):
            raise ValueError("Agent token must be distinct and contain at least 32 characters")
        if self.environment == "production" and not all(
            [
                *tokens,
                self.postgres_password.get_secret_value(),
                self.qdrant_api_key.get_secret_value(),
            ]
        ):
            raise ValueError("Production requires database, vector and service credentials")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_database,
        )
