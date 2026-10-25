"""Validate the main API against the configured database, source files and Qdrant.

This imports real documents as internal material and leaves their version/release
history in place. It creates no alternate database, tenant, app or demo corpus.
"""

import argparse
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from rag_portfolio.config import Settings
from rag_portfolio.db.models import BackgroundJob, KnowledgeBase
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.main import create_app
from rag_portfolio.services.jobs import JobQueue
from rag_portfolio.worker import run_once


async def drain(settings):
    database = Database(settings)
    queue = JobQueue(
        database.sessions,
        settings.tenant_id,
        settings.worker_lease_seconds,
        settings.worker_max_attempts,
    )
    try:
        while True:
            if await run_once(queue, database=database, settings=settings):
                continue
            async with database.sessions() as session:
                pending = await session.scalar(
                    select(BackgroundJob.id)
                    .where(
                        BackgroundJob.tenant_id == settings.tenant_id,
                        BackgroundJob.status.in_(["queued", "running"]),
                    )
                    .limit(1)
                )
            if pending is None:
                break
            await asyncio.sleep(2)
    finally:
        await database.close()


def run(client, method, url, **kwargs):
    response = getattr(client, method)(url, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {url}: {response.status_code} {response.text[:300]}")
    return response.json()


async def find_base(settings):
    database = Database(settings)
    try:
        async with database.sessions() as session:
            kb = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == settings.tenant_id,
                    KnowledgeBase.code == "rehab_kb",
                    KnowledgeBase.scope_key == "shared",
                )
            )
            if kb is None:
                raise RuntimeError("Create the shared rehab_kb using the main API first")
            return kb.id
    finally:
        await database.close()


def main(args):
    settings = Settings()
    kb_id = asyncio.run(find_base(settings), loop_factory=loop_factory)
    headers = {"Authorization": "Bearer " + settings.admin_token.get_secret_value()}
    summary = {"kb_id": str(kb_id), "documents": [], "queries": []}
    with TestClient(create_app(settings), backend_options={"loop_factory": loop_factory}) as client:
        sources = sorted(Path(args.source).glob("*.pdf"))[: args.limit]
        if not sources:
            raise RuntimeError("No PDFs found")
        for path in sources:
            print(f"Ingesting {path.name}", flush=True)
            job = run(
                client,
                "post",
                f"/v1/knowledge-bases/{kb_id}/documents",
                headers=headers,
                params={
                    "filename": path.name,
                    "title": path.stem,
                    "external_key": path.stem.split("__")[0],
                    "audiences": "internal",
                },
                content=path.read_bytes(),
            )
            if job["status"] == "failed":
                run(client, "post", f"/v1/jobs/{job['id']}/retry", headers=headers)
            asyncio.run(drain(settings), loop_factory=loop_factory)
            result = run(client, "get", f"/v1/jobs/{job['id']}", headers=headers)
            summary["documents"].append(result)
            if result["status"] != "succeeded":
                raise RuntimeError(f"Ingestion failed: {result['last_error_code']}")
        versions = run(client, "get", f"/v1/knowledge-bases/{kb_id}/versions", headers=headers)
        version_ids = [row["result_json"]["version_id"] for row in summary["documents"]]
        for version in versions:
            if version["id"] not in version_ids or version["review_status"] != "pending":
                continue
            run(
                client,
                "post",
                f"/v1/versions/{version['id']}/approve",
                headers=headers,
                json={
                    "expected_row_version": version["row_version"],
                    "expected_content_hash": version["content_hash"],
                    "expected_metadata_hash": version["metadata_hash"],
                    "reason": "Internal technical ingestion/source validation; no clinical signoff",
                },
            )
        requested = run(
            client,
            "post",
            f"/v1/knowledge-bases/{kb_id}/releases",
            headers=headers,
            json={"version_ids": version_ids},
        )
        print("Building main release index", flush=True)
        asyncio.run(drain(settings), loop_factory=loop_factory)
        build = run(client, "get", f"/v1/jobs/{requested['job_id']}", headers=headers)
        if build["status"] != "succeeded":
            raise RuntimeError(f"Index build failed: {build['last_error_code']}")
        rid = requested["release"]["id"]
        summary["release"] = run(
            client,
            "post",
            f"/v1/releases/{rid}/activate",
            headers=headers,
            json={"reason": "Activate verified internal source index"},
        )
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        cases = cases.get("cases", cases) if isinstance(cases, dict) else cases
        for case in cases[: args.queries]:
            question = case.get("query", case.get("question"))
            if not question:
                continue
            row = {"id": case.get("id"), "strategies": {}}
            for strategy in ("dense", "sparse", "hybrid"):
                response = run(
                    client,
                    "post",
                    "/v1/retrieve",
                    headers=headers,
                    json={
                        "kb_ids": [str(kb_id)],
                        "queries": [{"q": question}],
                        "strategy": strategy,
                        "rerank": False,
                    },
                )
                row["strategies"][strategy] = {
                    "evidence_count": len(response["evidence"]),
                    "chunk_ids": [item["chunk_id"] for item in response["evidence"]],
                    "pages": [item["page"] for item in response["evidence"]],
                    "trace_id": response["trace_id"],
                    "notes": response["notes"],
                }
                if not response["evidence"]:
                    raise RuntimeError(f"No evidence for {strategy}: {question}")
            summary["queries"].append(row)
        reranked = run(
            client,
            "post",
            "/v1/retrieve",
            headers=headers,
            json={
                "kb_ids": [str(kb_id)],
                "queries": [{"q": cases[0]["query"]}],
                "strategy": "hybrid",
                "rerank": True,
            },
        )
        if (
            not reranked["evidence"]
            or "reranker_unavailable_using_fusion_order" in reranked["notes"]
        ):
            raise RuntimeError("Real reranker verification failed")
        summary["reranker_check"] = {"trace_id": reranked["trace_id"], "notes": reranked["notes"]}
        # The normal reader is a patient principal and must not see internal sources.
        reader = {"Authorization": "Bearer " + settings.service_token.get_secret_value()}
        hidden = run(
            client,
            "post",
            "/v1/retrieve",
            headers=reader,
            json={
                "kb_ids": [str(kb_id)],
                "queries": [{"q": "rehabilitation"}],
                "strategy": "sparse",
                "rerank": False,
            },
        )
        assert not hidden["evidence"], "Internal material crossed the audience boundary"
        summary["audience_check"] = "passed"
    output = Path(settings.asset_root) / "validation" / "llamaindex-first-pass.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Saved {output}; {len(sources)} documents, {len(summary['queries'])} queries", flush=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="doc/selected")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--queries", type=int, default=6)
    parser.add_argument("--cases", default="eval/medical_rehab_v3/cases.json")
    main(parser.parse_args())
