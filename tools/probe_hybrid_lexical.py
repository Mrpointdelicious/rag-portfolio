"""Supplementary frozen cross-language probe; does not alter the original Gold suite."""

import argparse
import asyncio
import json
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
from eval_hybrid_service import ROOT, environment, free_port, ready

from rag_portfolio.services.hybrid_contract import digest


async def main(args):
    if args.output.exists():
        raise SystemExit("Use a new output directory")
    args.output.mkdir(parents=True)
    raw = (ROOT / "tests/fixtures/hybrid_eval/lexical-probe.json").read_bytes()
    probe = json.loads(raw)
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
    try:
        await ready(url, process)
        async with httpx.AsyncClient(trust_env=False, timeout=16) as client:
            for strategy in ("bm25", "dense", "hybrid", "hybrid_rerank", "hybrid_selection"):
                rid = str(uuid4())
                payload = dict(
                    schema_version="retrieval-v1",
                    request_id=rid,
                    query=probe["query"],
                    domain="help",
                    scope_key="hybrid-demo",
                    corpus_version="technical-hybrid-v1",
                    corpus_sha256=env["RAG_HYBRID_CORPUS_SHA256"],
                    index_sha256=env["RAG_HYBRID_INDEX_SHA256"],
                    strategy=strategy,
                    top_k=6,
                    context_budget=10000,
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
                        "strategy": strategy,
                        "http": response.status_code,
                        "response": body,
                        "expected_ref_present": any(
                            s["doc_ref"] == probe["expected_ref"] for s in body.get("evidence", [])
                        ),
                    }
                )
        report = {
            "execution": "real provider + real TCP; supplementary probe, original Gold unchanged",
            "probe_sha256": digest(raw),
            "probe": probe,
            "cases": rows,
        }
        (args.output / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print([(r["strategy"], r["expected_ref_present"]) for r in rows])
    finally:
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
