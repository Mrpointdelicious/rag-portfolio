import pytest
from pydantic import ValidationError

from rag_portfolio.config import Settings


def test_production_requires_credentials():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            service_token="",
            admin_token="",
            postgres_password="",
            qdrant_api_key="",
        )


def test_admin_and_reader_must_not_share_token():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, service_token="a" * 40, admin_token="a" * 40)


def test_url_encodes_password_without_string_concatenation():
    settings = Settings(_env_file=None, postgres_password="a@b:/?c")
    assert settings.database_url.password == "a@b:/?c"
    assert "a@b" not in str(settings.database_url)


def test_embedding_config_uses_rag_prefix_and_masks_secret(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "fake-embedding-test-key")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSION", "512")
    monkeypatch.setenv("RAG_EMBEDDING_TIMEOUT_SECONDS", "12")
    settings = Settings(_env_file=None)
    assert settings.embedding_dimension == 512
    assert settings.embedding_timeout_seconds == 12
    assert settings.embedding_api_key.get_secret_value() == "fake-embedding-test-key"
    assert "fake-embedding-test-key" not in repr(settings)
