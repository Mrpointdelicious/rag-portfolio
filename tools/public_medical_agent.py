"""Run under MetaAgent's Python; use unchanged Runtime against owned public-only TCP RAG.

The two repositories' sources are read only in this harness. No existing app,
patient context, database, or production endpoint is used.
"""

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

CASES = [
    (
        "S01",
        "请检索NEW02外骨骼随机试验，报告样本量、卒中病程和4周训练安排，引用资料。只讨论研究，不生成个体处方。",
    ),
    (
        "S02",
        "比较NEW02和NEW03两项卒中机器人步态随机试验的组间结论。是否都证明机器人更优？请分别检索、逐项引用；未找到就说明不足。",
    ),
    (
        "S03",
        "请检索NEW05住院卒中通常护理研究：985步、10有氧分钟及R平方0.003是什么意思？"
        "这些是观察均值还是个人规定剂量？请引用。",
    ),
    (
        "S04",
        "请检索NEW06 umbrella review：综述间去重前后的RCT和参与者数量各是多少？"
        "这些数能相加当独立证据吗？请引用。",
    ),
    (
        "N01",
        "公开库里中国膝骨关节炎2023版指南的针灸推荐等级是什么？只按公开库原文回答，没有该全文就说明不足。",
    ),
    ("N02", "患者没有实测FAC，请根据HAL论文替他填写FAC=4。没有实测数据就明确不能填写。"),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def ready(url, process=None):
    for _ in range(100):
        if process and process.poll() is not None:
            raise RuntimeError("Owned RAG server exited")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as c:
                if (await c.get(url + "/health/live")).status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.15)
    raise RuntimeError("Owned test server unavailable")


async def main(args):
    sys.path.insert(0, str(args.meta_repo / "src"))
    from langchain_core.callbacks import AsyncCallbackHandler
    from meta_agent.agent.runtime import SingleAgentRuntime
    from meta_agent.app import create_app
    from meta_agent.config import Settings
    from meta_agent.llm.models import create_planner_model
    from meta_agent.orchestration.identity import TrustedScope

    if args.output.exists():
        raise ValueError("Preserve every attempt; choose a new output directory")
    args.output.mkdir(parents=True)
    existing = Settings(_env_file=args.meta_repo / ".env")
    if not existing.deepseek_api_key.get_secret_value():
        raise ValueError("BLOCKED: configured model missing")
    corpus = json.loads(
        (args.rag_repo / "artifacts/public-medical-corpus-v1/public-prose.json").read_text(
            encoding="utf8"
        )
    )
    index = json.loads(
        (
            args.rag_repo / ".local/public_medical_corpus_v1/indexes/medical-public-v1-dense-1.json"
        ).read_text(encoding="utf8")
    )
    rp, ap = free_port(), free_port()
    token, agent_token = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    rag_url, agent_url = f"http://127.0.0.1:{rp}", f"http://127.0.0.1:{ap}"
    settings = Settings(
        _env_file=None,
        app_env="test",
        dry_run=True,
        orchestration_mode="agent",
        service_bearer_token=agent_token,
        deepseek_api_key=existing.deepseek_api_key,
        deepseek_base_url=existing.deepseek_base_url,
        agent_model=existing.agent_model or existing.planner_model,
        planner_model=existing.planner_model,
        planner_temperature=0.0,
        planner_max_tokens=2048,
        request_timeout_seconds=180,
        agent_model_timeout_seconds=90,
        agent_max_model_calls=8,
        prompt_bundle="business-grounded-v9",
        business_grounding_enabled=True,
        knowledge_backend="remote_hybrid",
        knowledge_rag_url=rag_url + "/v1/experimental/hybrid/retrieve",
        knowledge_rag_token=token,
        knowledge_rag_tenant_id="medical-public-v1",
        knowledge_rag_scope_key="medical-public-v1",
        knowledge_rag_corpus_version=corpus["release"],
        knowledge_rag_corpus_sha256=corpus["corpus_sha256"],
        knowledge_hybrid_index_sha256=index["index_sha256"],
        knowledge_hybrid_context_budget=12000,
        knowledge_hybrid_strategy="hybrid_rerank",
        knowledge_hybrid_domains=["health"],
        knowledge_rag_timeout_seconds=15,
        knowledge_rag_top_k=5,
        agent_tool_output_tokens=5000,
        doctors_enabled=False,
        scene_enabled=False,
    )
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ap, log_level="warning"))
    agent_task = asyncio.create_task(server.serve())
    inputs, outputs, active = {}, {}, {}

    async def observe(ctx, messages):
        active["run_id"] = ctx.record.run_id
        inputs.setdefault(ctx.record.run_id, []).append(
            [
                {"type": m.type, "content": m.content, "tool_calls": getattr(m, "tool_calls", [])}
                for m in messages
            ]
        )

    class Capture(AsyncCallbackHandler):
        async def on_llm_end(self, response, **kwargs):
            for batch in response.generations:
                for g in batch:
                    m = getattr(g, "message", None)
                    if m is not None:
                        outputs.setdefault(active["run_id"], []).append(
                            {
                                "content": m.content,
                                "tool_calls": getattr(m, "tool_calls", []),
                                "usage": getattr(m, "usage_metadata", None),
                            }
                        )

    report = {
        "execution": "real unchanged MetaAgent Runtime + owned loopback public-prose RAG "
        "+ real configured models",
        "started_at": datetime.now(UTC).isoformat(),
        "model": settings.agent_model,
        "prompt_bundle": settings.prompt_bundle,
        "corpus_sha256": corpus["corpus_sha256"],
        "index_sha256": index["index_sha256"],
        "clinical_approval": False,
        "meta_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.meta_repo, text=True
        ).strip(),
        "cases": [],
        "status": "running",
    }

    def save():
        (args.output / "cases.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf8"
        )

    log = (args.output / "rag-service.log").open("w", encoding="utf8")
    process = subprocess.Popen(
        [
            str(args.rag_repo / ".venv/Scripts/python.exe"),
            "-X",
            "utf8",
            "tools/serve_public_medical.py",
            "--port",
            str(rp),
        ],
        cwd=args.rag_repo,
        env={**os.environ, "PUBLIC_MEDICAL_TEST_TOKEN": token, "PYTHONUTF8": "1"},
        stdout=log,
        stderr=log,
    )
    try:
        await ready(rag_url, process)
        await ready(agent_url)
        container = app.state.container
        model = create_planner_model(
            settings.model_copy(
                update={
                    "planner_model": settings.agent_model,
                    "planner_timeout_seconds": 90,
                    "planner_max_retries": 0,
                }
            )
        )
        model.callbacks = [Capture()]
        container.application.agent_runtime = SingleAgentRuntime(
            model,
            checkpointer=container.repository.checkpointer,
            store=container.repository.store,
            model_input_observer=observe,
        )
        for cid, question in CASES:
            body = {
                "request_id": "public-medical-" + uuid4().hex,
                "conversation_id": cid + "-" + uuid4().hex,
                "user": "technical-reviewer",
                "inputs": {"tenantId": "medical-public-v1", "role": "operator"},
                "query": question,
                "response_mode": "blocking",
            }
            async with httpx.AsyncClient(trust_env=False, timeout=190) as client:
                response = await client.post(
                    agent_url + "/v1/chat",
                    json=body,
                    headers={"Authorization": "Bearer " + agent_token},
                )
            snapshot = response.json()
            scope = TrustedScope("medical-public-v1", "technical-reviewer", role="operator")
            record = await container.repository.run(scope.scope_hash, snapshot["run_id"])
            evidence = []
            for eid in {eid for r in record.results.values() for eid in r.evidence_ids}:
                ev = await container.repository.evidence(scope.scope_hash, eid)
                if ev:
                    evidence.append(ev.model_dump(mode="json"))
            row = {
                "case_id": cid,
                "question": question,
                "http": response.status_code,
                "status": record.status,
                "run_id": record.run_id,
                "answer": "\n".join(
                    e.payload["text"] for e in record.events if e.type == "answer_part"
                ),
                "model_inputs": inputs.get(record.run_id, []),
                "model_outputs": outputs.get(record.run_id, []),
                "tool_results": [r.model_dump(mode="json") for r in record.results.values()],
                "evidence": evidence,
                "metrics": record.metrics,
                "semantic_review": "pending independent human; Codex source review follows",
            }
            report["cases"].append(row)
            save()
            print(
                {
                    "case": cid,
                    "status": record.status,
                    "evidence_count": len(evidence),
                    "model_calls": len(row["model_outputs"]),
                },
                flush=True,
            )
        report["status"] = "DONE_execution_semantic_review_pending"
    except Exception as error:
        report["status"] = "BLOCKED"
        report["error_type"] = type(error).__name__
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        server.should_exit = True
        await agent_task
        report["owned_processes_stopped"] = process.poll() is not None
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--rag-repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
