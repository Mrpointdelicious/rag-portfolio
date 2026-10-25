"""Check live PG qualification using existing sources; all temporary revocations are restored.

Qdrant and HTTP retrieval are real. Reranker/generation await boundaries are controlled
only to introduce concurrent revocation; this is not a model quality evaluation.
Run with other publication/revocation operations stopped.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from rag_portfolio.config import Settings
from rag_portfolio.db.models import DocumentVersion, KnowledgeBase
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.main import create_app
from rag_portfolio.services.documents import ActorContext, record_audit
from rag_portfolio.services.hybrid_retrieval import Reranker


def execute(coroutine):
    return asyncio.run(coroutine, loop_factory=loop_factory)


async def selected_base(settings):
    database = Database(settings)
    try:
        async with database.sessions() as session:
            base = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == settings.tenant_id,
                    KnowledgeBase.code == "rehab_kb",
                    KnowledgeBase.scope_key == "shared",
                    KnowledgeBase.enabled.is_(True),
                    KnowledgeBase.active_release_id.is_not(None),
                )
            )
            if base is None:
                raise RuntimeError("Need the existing active shared rehab_kb")
            return str(base.id)
    finally:
        await database.close()


async def revoke(settings, version_id, marker=None):
    database = Database(settings)
    try:
        async with database.sessions.begin() as session:
            version = await session.get(DocumentVersion, UUID(version_id), with_for_update=True)
            if version is None or version.tenant_id != settings.tenant_id:
                raise RuntimeError("Version missing")
            if marker is None:
                if version.revoked_at is not None:
                    raise RuntimeError("Version already revoked")
                version.revoked_at = datetime.now(UTC)
                result = version.revoked_at
            else:
                if version.revoked_at != marker:
                    raise RuntimeError("Concurrent revoke detected; refusing to overwrite")
                version.revoked_at, result = None, None
            version.row_version += 1
            record_audit(
                session,
                ActorContext(settings.tenant_id, "qualification-validation", uuid4()),
                event_type="version.validation_restored" if marker else "version.revoked",
                entity_type="document_version",
                entity_id=version.id,
                reason="Temporary live PG qualification validation; retained source/index",
            )
            return result
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    if not parser.parse_args().execute:
        parser.error("Pass --execute with other publication/revocation operators stopped")
    settings = Settings()
    kb_id = execute(selected_base(settings))
    headers = {"Authorization": "Bearer " + settings.admin_token.get_secret_value()}
    body = {
        "kb_ids": [kb_id],
        "queries": [{"q": "脑卒中立位平衡评定与康复训练"}],
        "strategy": "sparse",
        "rerank": False,
    }
    report = {"kb_id": kb_id, "checks": [], "await_boundaries": "controlled; not model quality"}
    with TestClient(create_app(settings), backend_options={"loop_factory": loop_factory}) as client:

        def request(**changes):
            response = client.post("/v1/retrieve", headers=headers, json=body | changes)
            response.raise_for_status()
            return response.json()

        baseline = request()
        if not baseline["evidence"]:
            raise RuntimeError("Need existing eligible evidence")
        version_id = baseline["evidence"][0]["version_id"]
        report["version_id"] = version_id
        marker = execute(revoke(settings, version_id))
        try:
            response = request()
            assert all(item["version_id"] != version_id for item in response["evidence"])
            report["checks"].append("revoked_version_excluded_without_removing_index")
        finally:
            execute(revoke(settings, version_id, marker))

        markers = []

        async def during_rerank(self, query, candidates, texts):
            markers.append(await revoke(settings, version_id))
            return candidates, {}

        try:
            with patch.object(Reranker, "rerank", during_rerank):
                response = request(rerank=True)
            assert markers and all(
                item["version_id"] != version_id for item in response["evidence"]
            )
            report["checks"].append("concurrent_revoke_during_rerank_excluded")
        finally:
            if markers:
                execute(revoke(settings, version_id, markers[-1]))

        markers.clear()

        async def during_answer(settings_arg, questions, evidence):
            assert any(str(item.version_id) == version_id for item in evidence)
            markers.append(await revoke(settings, version_id))
            return "This controlled answer must be discarded."

        try:
            with patch("rag_portfolio.services.retrieval.generate_answer", during_answer):
                response = request(answer_mode="answer")
            assert response["answer"] is None
            assert "evidence_changed_during_generation_retry_required" in response["notes"]
            assert all(item["version_id"] != version_id for item in response["evidence"])
            report["checks"].append("concurrent_revoke_during_answer_discards_answer")
        finally:
            if markers:
                execute(revoke(settings, version_id, markers[-1]))
        restored = request()
        assert any(item["version_id"] == version_id for item in restored["evidence"])
        report["checks"].append("original_eligibility_restored")
    output = Path(settings.asset_root) / "validation" / "online-qualifications.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
