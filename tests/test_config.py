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
