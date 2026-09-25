from unittest.mock import AsyncMock
from uuid import uuid4

from rag_portfolio.api.dependencies import session


def test_liveness_and_request_id(client):
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(response.headers["X-Request-ID"]) == 36


def test_retrieve_requires_authentication(client):
    response = client.post(
        "/v1/retrieve", json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_retrieval_is_explicitly_unimplemented(client, read_headers):
    response = client.post(
        "/v1/retrieve",
        headers=read_headers,
        json={"kb_ids": [str(uuid4())], "queries": [{"q": "test"}]},
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "retrieval_not_implemented"


def test_request_cannot_override_tenant_or_audience(client, read_headers):
    response = client.post(
        "/v1/retrieve",
        headers=read_headers,
        json={
            "kb_ids": [str(uuid4())],
            "queries": [{"q": "test"}],
            "tenant_id": "other",
            "audience": "internal",
        },
    )
    assert response.status_code == 422
    assert "other" not in response.text


def test_read_token_cannot_create_knowledge_base(client, read_headers):
    response = client.post("/v1/knowledge-bases", headers=read_headers, json={"code": "rehab_kb"})
    assert response.status_code == 403


def test_scene_base_requires_registered_space(client, admin_headers):
    response = client.post("/v1/knowledge-bases", headers=admin_headers, json={"code": "scene_kb"})
    assert response.status_code == 422


def test_reader_query_is_bound_to_server_scope(client, read_headers):
    captured = []

    class Result:
        def all(self):
            return []

    class Session:
        async def scalars(self, statement):
            captured.append(statement.compile().params)
            return Result()

    async def fake_session():
        yield Session()

    client.app.dependency_overrides[session] = fake_session
    response = client.get("/v1/knowledge-bases", headers={**read_headers, "X-Tenant-ID": "other"})
    assert response.status_code == 200
    assert captured[0]["tenant_id_1"] == "test"
    assert captured[0]["scope_key_1"] == ["shared"]


def test_readiness_fails_on_dependency_failure_without_leaking_details(client):
    client.app.state.vector.check = AsyncMock(side_effect=RuntimeError("sensitive-connection-data"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["qdrant"] == "unavailable"
    assert "sensitive" not in response.text
