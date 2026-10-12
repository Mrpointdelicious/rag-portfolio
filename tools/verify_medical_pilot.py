"""Real owned Docker pilot UAT: HTTP entry, citations, restart, rollback and bounded load."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / ".local/medical_business_v2/pilot"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(PILOT / "compose.env"),
    "-f",
    str(ROOT / "deploy/medical-pilot.compose.yaml"),
]


async def main(args):
    if args.output.exists():
        raise ValueError("Preserve all attempts")
    args.output.mkdir(parents=True)
    operators = json.loads((PILOT / "operator-access.json").read_text())
    config = json.loads((PILOT / "config.json").read_text())
    auth = httpx.BasicAuth("reviewer-a", operators["reviewer-a"])
    other = httpx.BasicAuth("reviewer-b", operators["reviewer-b"])
    report = dict(
        started_at=datetime.now(UTC).isoformat(),
        execution="real Docker services, real model, own PostgreSQL, HTTP operator entry",
        checks=[],
        clinical_approval=False,
        human_uat="NOT_ASSESSED",
        status="running",
    )

    def save():
        (args.output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )

    def check(name, passed, **details):
        report["checks"].append(dict(name=name, passed=passed, **details))
        save()
        print(name, passed, flush=True)
        if not passed:
            raise AssertionError(name)

    def compose(*parts):
        r = subprocess.run(
            COMPOSE + list(parts), cwd=ROOT, capture_output=True, text=True, timeout=90
        )
        if r.returncode:
            raise RuntimeError("Owned compose operation failed")

    async def ready(client):
        for _ in range(60):
            try:
                r = await client.get("/pilot/ready", auth=auth)
                if r.status_code == 200:
                    return r.json()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned pilot not ready")

    async def select(release):
        compose("stop", "agent")
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/prepare_medical_pilot.py"),
                "--meta-repo",
                str(args.meta_repo),
                "--release",
                release,
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
        )
        if r.returncode:
            raise RuntimeError("Owned release selection failed")
        compose("up", "-d", "agent")

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:18121", trust_env=False, timeout=195
        ) as client:
            await ready(client)
            check("unauthenticated_entry_denied", (await client.get("/pilot")).status_code == 401)
            page = await client.get("/pilot", auth=auth)
            check(
                "entry_page_and_source_controls",
                page.status_code == 200
                and "查看来源" in page.text
                and "请勿输入真实患者资料" in page.text,
            )
            check(
                "untrusted_origin_denied",
                (
                    await client.post(
                        "/pilot/chat",
                        auth=auth,
                        json={"query": "test"},
                        headers={"Origin": "https://untrusted.invalid"},
                    )
                ).status_code
                == 403,
            )
            check(
                "patient_identity_cannot_be_submitted",
                (
                    await client.post(
                        "/pilot/chat",
                        auth=auth,
                        json={"query": "test", "patient_id": "synthetic-probe"},
                    )
                ).status_code
                == 422,
            )
            question = (
                "请查 Aerobic minutes and step number remain low "
                "in inpatient stroke rehabilitation "
                "的研究设计并引用来源。"
            )
            started = time.perf_counter()
            r = await client.post("/pilot/chat", auth=auth, json={"query": question})
            answer = r.json()
            (args.output / "first-answer.json").write_text(
                json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf8"
            )
            check(
                "real_entry_to_answer_and_citations",
                r.status_code == 200
                and bool(answer.get("citations"))
                and bool(answer.get("answer")),
                elapsed_ms=(time.perf_counter() - started) * 1000,
                status=answer.get("status"),
            )
            citation = answer["citations"][0]["url"]
            source = await client.get(citation, auth=auth)
            check(
                "source_resolves_to_valid_original_excerpt",
                source.status_code == 200 and bool(source.json().get("text")),
            )
            check(
                "other_operator_cannot_read_citation",
                (await client.get(citation, auth=other)).status_code == 404,
            )
            compose("restart", "agent")
            await ready(client)
            check(
                "evidence_survives_agent_restart",
                (await client.get(citation, auth=auth)).status_code == 200,
            )
            r = await client.post(
                "/pilot/chat",
                auth=auth,
                json={
                    "query": "再概括刚才的研究设计，保留来源。",
                    "conversation_id": answer["conversation_id"],
                },
            )
            follow = r.json()
            (args.output / "after-restart-answer.json").write_text(
                json.dumps(follow, ensure_ascii=False), encoding="utf8"
            )
            check(
                "conversation_survives_agent_restart",
                r.status_code == 200 and bool(follow.get("citations")),
            )
            compose("stop", "rag")
            check(
                "rag_outage_blocks_readiness",
                (await client.get("/pilot/ready", auth=auth)).status_code == 503,
            )
            check(
                "rag_outage_has_explicit_http_failure",
                (await client.post("/pilot/chat", auth=auth, json={"query": question})).status_code
                == 503,
            )
            compose("start", "rag")
            await ready(client)
            await select("public")
            public = await ready(client)
            check(
                "explicit_public_rollback_changes_both_pins",
                public["corpus_sha256"] != config["corpus_sha256"]
                and public["index_sha256"] != config["index_sha256"],
            )
            check(
                "old_combined_citation_not_reused",
                (await client.get(citation, auth=auth)).status_code == 410,
            )
            await select("combined")
            combined = await ready(client)
            check(
                "explicit_return_to_combined",
                combined["corpus_sha256"] == config["corpus_sha256"]
                and combined["index_sha256"] == config["index_sha256"],
            )
            # Direct service load uses the same public non-sensitive query; no patient text.
            headers = {
                "Authorization": "Bearer " + config["rag_token"],
                "X-Tenant-ID": "medical-business-v1",
                "X-Actor-Role": "operator",
                "X-Patient-Context": "false",
            }
            from uuid import uuid4

            payload = dict(
                schema_version="retrieval-v1",
                query=question,
                domain="health",
                scope_key="medical-business-v1",
                corpus_version=config["corpus_version"],
                corpus_sha256=config["corpus_sha256"],
                index_sha256=config["index_sha256"],
                strategy="hybrid_rerank",
                context_budget=12000,
                top_k=5,
            )
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:18120", trust_env=False, timeout=35
            ) as rag:
                start = time.perf_counter()
                responses = await asyncio.gather(
                    *[
                        rag.post(
                            "/v1/experimental/hybrid/retrieve",
                            headers=headers,
                            json={**payload, "request_id": str(uuid4())},
                        )
                        for _ in range(6)
                    ]
                )
                codes = [r.status_code for r in responses]
                stats = (await rag.get("/internal/stats", headers=headers)).json()
                check(
                    "six_way_load_is_bounded",
                    all(c in [200, 429] for c in codes)
                    and 200 in codes
                    and 429 in codes
                    and stats["active"] == 0,
                    codes=codes,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    stats=stats,
                )
            check("rotating_metadata_log_created", (PILOT / "logs/requests.jsonl").exists())
        report["status"] = "PASS_technical_human_UAT_pending"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
