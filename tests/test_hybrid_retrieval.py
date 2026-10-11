"""Contract-only fake providers; real execution is recorded separately by the TCP harness."""

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from rag_portfolio.adapters.embedding import EmbeddingAdapterError
from rag_portfolio.api.hybrid import create_app
from rag_portfolio.config import Settings
from rag_portfolio.services.hybrid_contract import RetrievalQuery, canonical, digest
from rag_portfolio.services.hybrid_retrieval import (
    Corpus,
    HybridPipeline,
    HybridSettings,
    Reranker,
    chunks,
    lexical_support,
    validate_vector,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures/hybrid_demo/corpus.json"
INDEX = ROOT / "fixtures/hybrid_demo/index.json"
TOKEN = "contract-only-" + "t" * 40


def config(**kw):
    return HybridSettings(
        enabled=True,
        token=TOKEN,
        corpus_path=str(CORPUS),
        corpus_sha256=digest(CORPUS.read_bytes()),
        index_path=str(INDEX),
        index_sha256=digest(INDEX.read_bytes()),
        **kw,
    )


def query(**kw):
    c = config()
    return RetrievalQuery(
        request_id=uuid4(),
        query="索引方案甲的构建方法",
        domain="help",
        scope_key=c.scope_key,
        corpus_version="technical-hybrid-v1",
        corpus_sha256=c.corpus_sha256,
        index_sha256=c.index_sha256,
        **kw,
    )


class FakeEmbedding:
    def __init__(self, bad=False):
        self.bad = bad

    async def embed_query(self, text):
        if self.bad:
            raise EmbeddingAdapterError("injected provider failure")
        # Contract stub only; not counted as real Dense acceptance.
        return [1.0] + [0.0] * 1023

    async def close(self):
        pass


class FakeReranker:
    async def rerank(self, *args):
        raise ValueError("injected rerank failure")

    async def close(self):
        pass


def pipeline(c=None, **kw):
    return HybridPipeline(
        c or config(),
        Settings(_env_file=None),
        embedding=FakeEmbedding(**kw),
        reranker=FakeReranker(),
    )


@pytest.mark.parametrize(
    "vector,dimension",
    [([0.0, 0.0], 2), ([1.0], 2), ([float("nan")], 1), ([float("inf")], 1), ([True], 1)],
)
def test_invalid_vector_dimension_norm_finite(vector, dimension):
    with pytest.raises(ValueError):
        validate_vector(vector, dimension)


def test_manifest_hash_span_page_approval():
    c = Corpus.model_validate_json(CORPUS.read_bytes())
    assert len(c.documents) == 8 and len(chunks(c)) == 15
    for mutation in (
        lambda d: d["documents"][0].update(text_sha256="0" * 64),
        lambda d: d["documents"][0]["sections"][0].update(end=99999),
        lambda d: d["documents"][0].update(approved=False),
        lambda d: d["documents"][0]["sections"][0].update(page=0),
    ):
        body = c.model_dump(mode="json")
        mutation(body)
        with pytest.raises(ValueError):
            Corpus.model_validate(body)


def test_drift_pin_model_and_chunk_hash_fail_closed(tmp_path):
    c = config()
    p = tmp_path / "index.json"
    p.write_bytes(INDEX.read_bytes() + b" ")
    with pytest.raises(ValueError):
        pipeline(c.model_copy(update={"index_path": str(p)}))
    import json

    body = json.loads(INDEX.read_bytes())
    body["chunk_hashes"][next(iter(body["chunk_hashes"]))] = "0" * 64
    p.write_bytes(canonical(body))
    c = c.model_copy(update={"index_path": str(p), "index_sha256": digest(p.read_bytes())})
    with pytest.raises(ValueError):
        pipeline(c)
    with pytest.raises(ValueError):
        HybridPipeline(config(), Settings(_env_file=None, embedding_dimension=512))


@pytest.mark.asyncio
async def test_explicit_dense_rerank_degradation_and_atomic_budget():
    p = pipeline(bad=True)
    r = await p.search(query())
    assert r.status == "partial" and not r.rerank_applied
    assert r.model_calls == {"embedding": 1, "rerank": 1}
    assert r.degradation == ["dense_unavailable", "reranker_unavailable"]
    for s in r.evidence:
        assert s.end - s.start == len(s.text)
        assert hashlib.sha256(s.text.encode()).hexdigest() == s.text_sha256
    small = await p.search(query(context_budget=100))
    assert small.status == "unavailable" and small.code == "context_overflow"
    assert small.selected_count == 0 and small.budget_truncated
    await p.close()


@pytest.mark.asyncio
async def test_no_domain_no_model_calls_and_version_mismatch():
    p = pipeline()
    q = query().model_copy(update={"domain": "health"})
    r = await p.search(q)
    assert r.code == "no_evidence" and not any(r.model_calls.values())
    with pytest.raises(ValueError, match="version_unavailable"):
        await p.search(query().model_copy(update={"corpus_version": "other"}))
    await p.close()


def test_old_no_answer_particle_overlap_counterexample():
    assert not lexical_support(
        "zznonexistentquasar 的恒星核聚变温度是多少？",
        "方案乙没有公布实测吞吐量，也没有证明答案准确率。",
    )
    assert lexical_support("索引方案甲怎样构建？", "索引方案甲在构建时生成向量。")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("auth", 401),
        ("scope", 403),
        ("patient", 403),
        ("version", 409),
        ("request", 422),
        ("budget", 422),
        ("size", 413),
    ],
)
async def test_http_authority_and_limits(mutation, expected):
    p = pipeline()
    app = create_app(config(), Settings(_env_file=None), pipeline=p)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://contract"
        ) as client,
    ):
        q = query(strategy="bm25")
        body = q.model_dump(mode="json")
        headers = {
            "Authorization": "Bearer " + TOKEN,
            "X-Tenant-ID": "hybrid-demo",
            "X-Actor-Role": "operator",
            "X-Patient-Context": "false",
            "X-Request-ID": str(q.request_id),
        }
        if mutation == "auth":
            headers["Authorization"] = "Bearer wrong"
        if mutation == "scope":
            body["scope_key"] = "private"
        if mutation == "patient":
            headers["X-Patient-Context"] = "true"
        if mutation == "version":
            body["index_sha256"] = "0" * 64
        if mutation == "request":
            headers["X-Request-ID"] = str(uuid4())
        if mutation == "budget":
            body["context_budget"] = True
        if mutation == "size":
            body["query"] = "x" * 25000
        r = await client.post("/v1/experimental/hybrid/retrieve", json=body, headers=headers)
        assert r.status_code == expected


@pytest.mark.asyncio
async def test_http_signed_result_and_runtime_drift(tmp_path):
    import hmac

    corpus = tmp_path / "corpus.json"
    corpus.write_bytes(CORPUS.read_bytes())
    c = config().model_copy(update={"corpus_path": str(corpus)})
    app = create_app(c, Settings(_env_file=None), pipeline=pipeline(c))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://contract"
        ) as client,
    ):
        q = query(strategy="bm25")
        headers = {
            "Authorization": "Bearer " + TOKEN,
            "X-Tenant-ID": "hybrid-demo",
            "X-Actor-Role": "operator",
            "X-Patient-Context": "false",
            "X-Request-ID": str(q.request_id),
        }
        r = await client.post(
            "/v1/experimental/hybrid/retrieve", json=q.model_dump(mode="json"), headers=headers
        )
        body = r.json()
        sig = body.pop("signature")
        assert r.status_code == 200 and hmac.compare_digest(
            sig, hmac.new(TOKEN.encode(), canonical(body), hashlib.sha256).hexdigest()
        )
        corpus.write_bytes(corpus.read_bytes() + b" ")
        assert (await client.get("/health/live")).status_code == 503
        assert (
            await client.post(
                "/v1/experimental/hybrid/retrieve", json=q.model_dump(mode="json"), headers=headers
            )
        ).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "results",
    [
        [{"index": 0, "relevance_score": 0.7}, {"index": 0, "relevance_score": 0.8}],
        [{"index": 4, "relevance_score": 0.7}],
        [{"index": 0, "relevance_score": float("inf")}],
        [],
    ],
)
async def test_reranker_malformed_indices_and_scores(results):
    import json

    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=json.dumps({"output": {"results": results}}))
    )
    provider = Settings(
        _env_file=None,
        embedding_base_url="https://provider/api/v1",
        embedding_api_key="fake-contract",
    )
    reranker = Reranker(provider, config(), transport=transport)
    with pytest.raises(ValueError, match="reranker_unavailable"):
        await reranker.rerank("test", [SimpleNamespace(chunk_id=uuid4(), score=1.0)], ["test"])
    await reranker.close()


@pytest.mark.asyncio
async def test_reranker_actual_index_mapping_contract():
    from rag_portfolio.services.retrieval_algorithms import Candidate

    candidates = [Candidate(uuid4(), 1.0), Candidate(uuid4(), 2.0)]
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.2},
                    ]
                }
            },
        )
    )
    provider = Settings(
        _env_file=None,
        embedding_base_url="https://provider/api/v1",
        embedding_api_key="fake-contract",
    )
    rr = Reranker(provider, config(), transport=transport)
    result, _ = await rr.rerank("q", candidates, ["a", "b"])
    assert result[0] == replace(candidates[1], score=0.9)
    await rr.close()


def test_runtime_has_no_evaluation_import_or_report_loader():
    root = ROOT / "src/rag_portfolio"
    for p in [
        root / "services/hybrid_retrieval.py",
        root / "services/retrieval_algorithms.py",
        root / "api/hybrid.py",
    ]:
        text = p.read_text(encoding="utf8")
        assert "rag_portfolio.evaluation" not in text
        assert "ArchivedDenseRetriever" not in text
        assert "gold.json" not in text and "results.json" not in text


def test_default_disabled_and_production_blocked():
    assert not HybridSettings().enabled
    with pytest.raises(ValueError):
        config(environment="production")


@pytest.mark.asyncio
async def test_sparse_failure_and_service_request_budget_are_explicit():
    from rag_portfolio.services.errors import EvaluationError

    p = pipeline(config(max_requests=1))

    async def embed(text):
        return next(iter(p.index.vectors.values()))

    async def sparse_fail(*args, **kwargs):
        raise EvaluationError("injected sparse failure")

    p.embedding.embed_query = embed
    p.sparse["help"].search = sparse_fail
    r = await p.search(query(strategy="hybrid"))
    assert r.status == "partial" and r.degradation == ["bm25_unavailable"]
    assert r.model_calls == {"embedding": 1, "rerank": 0}
    with pytest.raises(ValueError, match="provider_request_budget_exhausted"):
        await p.search(query(strategy="hybrid"))
    await p.close()
