import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.main import create_app

READ_TOKEN = "read-token-for-tests-" + "a" * 32
ADMIN_TOKEN = "admin-token-for-tests-" + "b" * 32


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None,
        environment="test",
        service_token=READ_TOKEN,
        admin_token=ADMIN_TOKEN,
        tenant_id="test",
    )
    with TestClient(
        create_app(settings), backend_options={"loop_factory": loop_factory}
    ) as instance:
        yield instance


@pytest.fixture
def read_headers():
    return {"Authorization": f"Bearer {READ_TOKEN}"}


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
async def database():
    name = os.environ.get("RAG_TEST_DATABASE", "")
    if not name:
        pytest.skip("Use uv run python tools/test.py for isolated PostgreSQL tests")
    settings = Settings()
    assert name.startswith("rag_test_") and settings.postgres_database == name
    instance = Database(settings)
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def tenant():
    return "test_" + uuid4().hex


def pytest_asyncio_loop_factories():
    return {"selector": loop_factory}
