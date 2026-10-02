import json
import traceback

import httpx
import pytest

from rag_portfolio.adapters.embedding import EmbeddingAdapter, EmbeddingAdapterError
from rag_portfolio.config import Settings


def settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "embedding_provider": "alibaba_dashscope",
            "embedding_base_url": "https://embedding.invalid/api/v1/",
            "embedding_api_key": "fake-embedding-key-for-tests",
            "embedding_model": "qwen3.7-text-embedding",
            "embedding_dimension": 3,
            "embedding_output_type": "dense",
            "embedding_timeout_seconds": 7,
            **overrides,
        },
    )


def response_body(vectors):
    return {
        "output": {
            "embeddings": [
                {"text_index": index, "embedding": vector} for index, vector in enumerate(vectors)
            ]
        }
    }


@pytest.mark.parametrize("text_type", ["document", "query"])
async def test_native_request_parameters_and_client_cleanup(text_type):
    def handle(request):
        assert str(request.url) == (
            "https://embedding.invalid/api/v1/services/embeddings/text-embedding/text-embedding"
        )
        assert json.loads(request.content) == {
            "model": "qwen3.7-text-embedding",
            "input": {"texts": ["测试文本"]},
            "parameters": {"text_type": text_type, "dimension": 3, "output_type": "dense"},
        }
        assert request.headers["Authorization"] == "Bearer fake-embedding-key-for-tests"
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=response_body([[0.1, 0.2, 0.3]]))

    adapter = EmbeddingAdapter(settings(), transport=httpx.MockTransport(handle))
    try:
        if text_type == "document":
            assert await adapter.embed_documents(["测试文本"]) == [[0.1, 0.2, 0.3]]
        else:
            assert await adapter.embed_query("测试文本") == [0.1, 0.2, 0.3]
    finally:
        await adapter.close()
    assert adapter.client.is_closed


async def test_multiple_documents_follow_text_index_not_response_order():
    body = response_body([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    body["output"]["embeddings"].reverse()
    adapter = EmbeddingAdapter(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        assert await adapter.embed_documents(["一", "二", "三"]) == [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (response_body([[1, 2]]), "dimension mismatch"),
        (response_body([]), "count mismatch"),
        (response_body([[1, 2, 3], [1, 2, 3]]), "count mismatch"),
        ({}, "output.embeddings"),
        ([], "output.embeddings"),
        ({"output": None}, "output.embeddings"),
        ({"output": {"embeddings": {}}}, "output.embeddings"),
        ({"output": {"embeddings": [None]}}, "response item"),
        ({"output": {"embeddings": [{"embedding": [1, 2, 3]}]}}, "text_index"),
        ({"output": {"embeddings": [{"text_index": True, "embedding": [1, 2, 3]}]}}, "text_index"),
        ({"output": {"embeddings": [{"text_index": 1, "embedding": [1, 2, 3]}]}}, "text_index"),
        ({"output": {"embeddings": [{"text_index": 0}]}}, "must be an array"),
        (response_body([[1, "2", 3]]), "finite numbers"),
        (response_body([[1, True, 3]]), "finite numbers"),
    ],
)
async def test_malformed_responses_raise_adapter_error(body, message):
    adapter = EmbeddingAdapter(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(EmbeddingAdapterError, match=message):
            await adapter.embed_query("test")
    finally:
        await adapter.close()


async def test_duplicate_text_index_is_rejected():
    body = response_body([[1, 2, 3], [4, 5, 6]])
    body["output"]["embeddings"][1]["text_index"] = 0
    adapter = EmbeddingAdapter(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(EmbeddingAdapterError, match="text_index"):
            await adapter.embed_documents(["a", "b"])
    finally:
        await adapter.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**400])
async def test_nonfinite_and_overflow_numbers_are_rejected(value):
    content = json.dumps(response_body([[value, 0, 0]]))
    adapter = EmbeddingAdapter(
        settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    try:
        with pytest.raises(EmbeddingAdapterError, match="finite numbers"):
            await adapter.embed_query("test")
    finally:
        await adapter.close()


@pytest.mark.parametrize("texts", [[], [""], ["  "], ["valid", ""]])
async def test_empty_input_never_makes_a_request(texts):
    def unexpected_request(_):
        pytest.fail("Empty input must not be sent to the provider")

    adapter = EmbeddingAdapter(settings(), transport=httpx.MockTransport(unexpected_request))
    try:
        with pytest.raises(EmbeddingAdapterError, match="nonempty"):
            await adapter.embed_documents(texts)
        with pytest.raises(EmbeddingAdapterError, match="nonempty"):
            await adapter.embed_query(" ")
    finally:
        await adapter.close()


@pytest.mark.parametrize("failure", [302, 401, 429, 500, "timeout", "network", "json"])
async def test_http_failures_are_sanitized(failure):
    fake_key = settings().embedding_api_key.get_secret_value()

    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(fake_key, request=request)
        if failure == "network":
            raise httpx.ConnectError(fake_key, request=request)
        return httpx.Response(200 if failure == "json" else failure, text=fake_key)

    adapter = EmbeddingAdapter(settings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(EmbeddingAdapterError) as caught:
            await adapter.embed_query("test")
        assert fake_key not in "".join(traceback.format_exception(caught.value))
        if isinstance(failure, int):
            assert str(failure) in str(caught.value)
        elif failure == "timeout":
            assert "7s" in str(caught.value)
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_provider": "other"},
        {"embedding_output_type": "sparse"},
        {"embedding_base_url": ""},
        {"embedding_api_key": ""},
        {"embedding_model": ""},
    ],
)
def test_missing_or_unsupported_configuration_is_rejected(overrides):
    with pytest.raises(EmbeddingAdapterError):
        EmbeddingAdapter(settings(**overrides))
