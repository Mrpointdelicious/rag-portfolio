from typing import Literal

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
    service_token: SecretStr = SecretStr("")
    admin_token: SecretStr = SecretStr("")
    read_scope_keys: list[str] = Field(default_factory=lambda: ["shared"], min_length=1)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    dependency_timeout_seconds: float = Field(default=3, ge=0.1, le=30)

    @model_validator(mode="after")
    def validate_credentials(self) -> "Settings":
        tokens = [self.service_token.get_secret_value(), self.admin_token.get_secret_value()]
        if any(token and len(token) < 32 for token in tokens):
            raise ValueError("Service tokens must contain at least 32 characters")
        if tokens[0] and tokens[0] == tokens[1]:
            raise ValueError("Read and admin tokens must be different")
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
