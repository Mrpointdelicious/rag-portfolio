"""Real TCP ablation on separately frozen public Gold; never imported by runtime."""

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from rag_portfolio.services.hybrid_contract import digest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def environment(token):
    corpus, index = (
        ROOT / "fixtures/hybrid_demo/corpus.json",
        ROOT / "fixtures/hybrid_demo/index.json",
    )
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "RAG_HYBRID_ENABLED": "true",
        "RAG_HYBRID_ENVIRONMENT": "test",
        "RAG_HYBRID_TOKEN": token,
        "RAG_HYBRID_CORPUS_PATH": str(corpus),
        "RAG_HYBRID_CORPUS_SHA256": digest(corpus.read_bytes()),
        "RAG_HYBRID_INDEX_PATH": str(index),
        "RAG_HYBRID_INDEX_SHA256": digest(index.read_bytes()),
    }


async def ready(url, process):
    async with httpx.AsyncClient(trust_env=False) as client:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Owned hybrid process exited")
            try:
                if (await client.get(url + "/health/live", timeout=1)).status_code == 200:
                    return
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("Hybrid startup unavailable")


def metrics(refs, groups):
    hits = [any(r in refs for r in g) for g in groups]
    ranks = [i for i, r in enumerate(refs, 1) if any(r in g for g in groups)]
    return {
        "hit": bool(any(hits)),
        "recall": sum(hits) / len(groups),
        "complete": all(hits),
        "mrr": 1 / min(ranks) if ranks else 0,
    }


async def main(args):
    if args.output.exists():
        raise SystemExit("Use a fresh output directory; never overwrite a run")
    args.output.mkdir(parents=True)
    gold_path = ROOT / "tests/fixtures/hybrid_eval/gold.json"
    raw = gold_path.read_bytes()
    assert digest(raw) == (gold_path.parent / "gold.sha256").read_text().strip()
    gold = json.loads(raw)
    token, port = secrets.token_urlsafe(40), free_port()
    env = environment(token)
    url = f"http://127.0.0.1:{port}"
    log = (args.output / "service.log").open("w", encoding="utf8")
    process = subprocess.Popen(
        [
            str(ROOT / ".venv/Scripts/python.exe"),
            "-m",
            "rag_portfolio.hybrid_serve",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
    )
    rows = []
    lookup = json.loads((ROOT / "fixtures/hybrid_demo/corpus.json").read_bytes())
    from uuid import NAMESPACE_URL, uuid5

    refs = {
        str(
            uuid5(NAMESPACE_URL, f"{d['source']}@{d['version']}#{s['section_id']}")
        ): f"{d['code']}@{d['version']}#{s['section_id']}"
        for d in lookup["documents"]
        for s in d["sections"]
    }
    report = {
        "execution": "real-provider + real TCP; no query/rerank cache",
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "gold_sha256": digest(raw),
        "corpus_sha256": env["RAG_HYBRID_CORPUS_SHA256"],
        "index_sha256": env["RAG_HYBRID_INDEX_SHA256"],
        "cases": rows,
    }

    def save():
        (args.output / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )

    try:
        await ready(url, process)
        async with httpx.AsyncClient(trust_env=False, timeout=16) as client:
            for strategy in ("bm25", "dense", "hybrid", "hybrid_rerank", "hybrid_selection"):
                for c in gold["cases"]:
                    rid = str(uuid4())
                    payload = dict(
                        schema_version="retrieval-v1",
                        request_id=rid,
                        query=c["query"],
                        domain=c["domain"],
                        scope_key="hybrid-demo",
                        corpus_version="technical-hybrid-v1",
                        corpus_sha256=env["RAG_HYBRID_CORPUS_SHA256"],
                        index_sha256=env["RAG_HYBRID_INDEX_SHA256"],
                        strategy=strategy,
                        top_k=c["top_k"],
                        context_budget=c["context_budget"],
                    )
                    started = time.monotonic()
                    response = await client.post(
                        url + "/v1/experimental/hybrid/retrieve",
                        json=payload,
                        headers={
                            "Authorization": "Bearer " + token,
                            "X-Request-ID": rid,
                            "X-Tenant-ID": "hybrid-demo",
                            "X-Actor-Role": "operator",
                            "X-Patient-Context": "false",
                        },
                    )
                    body = response.json()
                    body.pop("signature", None)
                    row = {
                        "case": c["id"],
                        "strategy": strategy,
                        "query": c["query"],
                        "http": response.status_code,
                        "elapsed_ms": (time.monotonic() - started) * 1000,
                        "response": body,
                    }
                    if response.status_code == 200 and c["groups"]:
                        # Ranking quality is separate from packed context quality.
                        ranking = body["stages"]["selection"]
                        row["retrieval_at_6"] = metrics(
                            [refs[h["chunk_id"]] for h in ranking[:6]], c["groups"]
                        )
                        row["context"] = metrics(
                            [s["doc_ref"] for s in body["evidence"]], c["groups"]
                        )
                    if c["no_answer"]:
                        row["no_answer_correct"] = not body.get("evidence")
                    rows.append(row)
                    save()
                print(
                    {
                        "strategy": strategy,
                        "completed": len(rows),
                        "real_rerank_calls": sum(
                            r["response"].get("model_calls", {}).get("rerank", 0) for r in rows
                        ),
                    },
                    flush=True,
                )
            # These new paraphrases are deliberately outside the frozen query suite.
            for i, query in enumerate(
                (
                    "方案乙离线建向量以后，在线收到一个陌生提问会怎样寻找原文？",
                    "如果演示消息队列的写入方把读取方甩在后面，应如何约束写入速度？",
                ),
                1,
            ):
                rid = str(uuid4())
                payload.update(
                    request_id=rid, query=query, strategy="hybrid_selection", context_budget=10000
                )
                response = await client.post(
                    url + "/v1/experimental/hybrid/retrieve",
                    json=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-Request-ID": rid,
                        "X-Tenant-ID": "hybrid-demo",
                        "X-Actor-Role": "operator",
                        "X-Patient-Context": "false",
                    },
                )
                body = response.json()
                body.pop("signature", None)
                rows.append(
                    {
                        "case": f"new-input-{i}",
                        "query": query,
                        "http": response.status_code,
                        "response": body,
                    }
                )
                save()
    finally:
        save()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
