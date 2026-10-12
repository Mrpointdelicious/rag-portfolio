"""Run the sealed 60-condition contract suite over four owned loopback HTTP services."""

import argparse
import asyncio
import copy
import hashlib
import hmac
import json
import secrets
import socket
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    url = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("Owned server stopped")
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("Owned server failed to start")
        yield url
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@asynccontextmanager
async def environment(
    path,
    *,
    retrieve_override=None,
    generator_override=None,
    release_override=None,
    release_state_override=None,
):
    from meta_agent.preprod_api import (
        AuthorityClient,
        bearer,
        create_agent_app,
        create_authority_app,
        create_patient_app,
    )
    from meta_agent.preprod_security import Rejected, State, sign

    from rag_portfolio.preproduction_service import create_preproduction_app

    state = State(path)
    keys = {a: secrets.token_urlsafe(40) for a in ["agent", "patient-tools", "knowledge"]}
    service_tokens = {a: secrets.token_urlsafe(40) for a in keys}
    response_key = secrets.token_urlsafe(40)
    generation = state.grant("syn-tenant", "syn-self", "syn-p-a")
    issued_at = int(time.time())
    claims = dict(
        schema_version="synthetic-subject-v1",
        issuer="owned-synthetic-authority",
        audience="agent",
        tenant="syn-tenant",
        actor="syn-self",
        role="patient",
        patient="syn-p-a",
        session="syn-session-a",
        purpose="record_and_literature",
        grant_generation=generation,
        issued_at=issued_at,
        expires_at=issued_at + 120,
    )
    measurement = dict(
        name="gait_speed",
        value=0.42,
        unit="m/s",
        measured_at=datetime.now(UTC).isoformat(),
        quality="observed",
    )
    record = dict(
        contract_version="synthetic-measurements-v1",
        tenant="syn-tenant",
        patient="syn-p-a",
        display_name="合成同名患者",
        measurements=[measurement],
    )
    records = {
        ("syn-tenant", "syn-p-a"): record,
        ("syn-tenant", "syn-p-b"): {
            **copy.deepcopy(record),
            "patient": "syn-p-b",
            "measurements": [{**measurement, "value": 0.77}],
        },
    }
    release = dict(
        active=True,
        release_id="syn-release-1",
        generation=1,
        corpus_sha256="c" * 64,
        index_sha256="i" * 64,
    )
    if release_override is not None:
        release.update(release_override)
    response_fault = None
    async with AsyncExitStack() as stack:
        auth_url = await stack.enter_async_context(
            serve(create_authority_app(state, keys, service_tokens))
        )
        authorities = {a: AuthorityClient(auth_url, service_tokens[a]) for a in keys}
        patient_app = create_patient_app(authorities["patient-tools"], records)
        patient_url = await stack.enter_async_context(serve(patient_app))

        async def authorize(header):
            try:
                return (await authorities["knowledge"].authorize(header)).model_dump()
            except Rejected as exc:
                raise HTTPException(exc.status, exc.code) from exc

        async def retrieve(query):
            if retrieve_override is not None:
                return await retrieve_override(query)
            return [
                dict(
                    doc_ref="syn-paper@v1#outcome",
                    text="自建合成文献：计划测量步行速度；文献示例0.93 m/s不是患者测量。",
                )
            ]

        knowledge_app = create_preproduction_app(
            authorize=authorize,
            release_state=release_state_override or (lambda: dict(release)),
            retrieve=retrieve,
            response_key=response_key,
        )
        knowledge_url = await stack.enter_async_context(serve(knowledge_app))

        async def generator(payload):
            if generator_override is not None:
                return await generator_override(payload)
            return "自建固定生成桩；不声称临床正确。"

        def agent_factory():
            return create_agent_app(
                state=State(path),
                keys=keys,
                authority=authorities["agent"],
                patient_url=patient_url,
                knowledge_url=knowledge_url,
                response_key=response_key,
                expected_pins={
                    k: release[k] for k in ["release_id", "corpus_sha256", "index_sha256"]
                },
                generator=generator,
            )

        agent_app = agent_factory()
        agent_url = await stack.enter_async_context(serve(agent_app))
        yield dict(
            state=state,
            claims=claims,
            keys=keys,
            token=lambda c: bearer(sign(c, keys[c["audience"]])),
            records=records,
            record=record,
            measurement=measurement,
            release=release,
            patient_app=patient_app,
            knowledge_app=knowledge_app,
            agent_app=agent_app,
            agent_factory=agent_factory,
            agent_url=agent_url,
            patient_url=patient_url,
            knowledge_url=knowledge_url,
            stack=stack,
            response_key=response_key,
            response_fault=response_fault,
        )


async def run_case(case, path):
    from meta_agent.preprod_api import bearer
    from meta_agent.preprod_security import CONCEPTS, canonical, sign

    scenario = case["scenario"]
    async with environment(path) as e, httpx.AsyncClient(trust_env=False, timeout=8) as client:
        claims = e["claims"]
        body = dict(task="record", session_id=claims["session"])
        url = e["agent_url"] + "/v1/preproduction/runs"
        header = None
        m = e["measurement"]
        assertions = {}
        if case["group"] == "identity":
            if scenario == "assigned_clinician":
                claims.update(
                    actor="syn-clinician",
                    role="clinician",
                    grant_generation=e["state"].grant(
                        "syn-tenant", "syn-clinician", "syn-p-a", "clinician"
                    ),
                )
            elif scenario == "missing_token":
                header = ""
            elif scenario == "bad_signature":
                value = sign(claims, e["keys"]["agent"])
                value["signature"] = "0" * 64
                header = bearer(value)
            elif scenario == "expired_token":
                claims.update(issued_at=int(time.time()) - 121, expires_at=int(time.time()) - 1)
            elif scenario == "future_token":
                claims.update(issued_at=int(time.time()) + 20, expires_at=int(time.time()) + 100)
            elif scenario == "wrong_audience":
                claims["audience"] = "patient-tools"
            elif scenario == "unknown_issuer":
                claims["issuer"] = "untrusted"
            elif scenario == "excessive_ttl":
                claims["expires_at"] = claims["issued_at"] + 121
            elif scenario == "cross_tenant":
                claims["tenant"] = "syn-other"
            elif scenario == "unassigned_patient":
                claims["patient"] = "syn-p-b"
            elif scenario == "forged_role":
                value = sign(claims, e["keys"]["agent"])
                value["claims"]["role"] = "clinician"
                header = bearer(value)
            elif scenario == "client_identity_field":
                body["patient_id"] = "syn-p-b"
            elif scenario == "wrong_purpose":
                claims["purpose"] = "administration"
            elif scenario == "wrong_session":
                body["session_id"] = "syn-other-session"
            elif scenario in {"revoked_grant", "old_grant_after_regrant"}:
                e["state"].grant("syn-tenant", "syn-self", "syn-p-a", active=False)
                if scenario == "old_grant_after_regrant":
                    e["state"].grant("syn-tenant", "syn-self", "syn-p-a")
            elif scenario == "operator_access":
                claims.update(
                    role="operator",
                    grant_generation=e["state"].grant(
                        "syn-tenant", "syn-self", "syn-p-a", "operator"
                    ),
                )
            elif scenario == "direct_patient_wrong_audience":
                url = e["patient_url"] + "/v1/preproduction/patient/measurements"
            elif scenario == "switch_patient_old_session":
                first = await client.post(
                    url, json=body, headers={"Authorization": e["token"](claims)}
                )
                assertions["first_patient_allowed"] = first.status_code == 200
                claims.update(
                    patient="syn-p-b",
                    grant_generation=e["state"].grant("syn-tenant", "syn-self", "syn-p-b"),
                )
        elif case["group"] == "facts":
            if scenario == "missing_value":
                m.pop("value")
            elif scenario == "null_value":
                m["value"] = None
            elif scenario == "placeholder_zero":
                m.update(value=0.0, quality="placeholder")
            elif scenario == "observed_zero":
                m["value"] = 0.0
            elif scenario == "missing_unit":
                m["unit"] = None
            elif scenario == "stale_measurement":
                m["measured_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
            elif scenario == "future_measurement":
                m["measured_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            elif scenario == "undated_measurement":
                m["measured_at"] = None
            elif scenario == "uncertain_measurement":
                m["quality"] = "uncertain"
            elif scenario == "no_measurement":
                e["record"]["measurements"] = []
            elif scenario == "same_name_patient_isolation":
                assertions["same_display_name"] = (
                    e["records"][("syn-tenant", "syn-p-b")]["display_name"]
                    == e["record"]["display_name"]
                )
            elif scenario == "malformed_number":
                m["value"] = {"fabricated": 1}
            elif scenario == "numeric_string":
                m["value"] = "0.42"
            elif scenario == "unknown_unit":
                m["unit"] = "unknown"
            elif scenario == "backend_subject_conflict":
                e["record"]["patient"] = "syn-p-b"
            elif scenario == "patient_backend_outage":
                e["patient_app"].state.available = False
            elif scenario == "patient_not_found":
                e["records"].pop(("syn-tenant", "syn-p-a"))
            elif scenario == "unexpected_backend_field":
                e["record"]["unapproved_history"] = "synthetic canary"
        else:
            body.update(task="literature", concept="robotic_gait")
            if scenario == "unknown_concept":
                body["concept"] = "anything"
            elif scenario == "free_query_rejected":
                body["query"] = "自由问题"
            elif scenario == "privacy_canary_rejected":
                body["query"] = "合成姓名与无效号码+86-000-0000-0000"
            elif scenario == "direct_rag_wrong_audience":
                url = e["knowledge_url"] + "/v1/preproduction/retrieve"
                body = dict(concept="robotic_gait", patient_context="synthetic")
            elif scenario == "rag_revoked_grant":
                url = e["knowledge_url"] + "/v1/preproduction/retrieve"
                body = dict(concept="robotic_gait", patient_context="synthetic")
                claims["audience"] = "knowledge"
                e["state"].grant("syn-tenant", "syn-self", "syn-p-a", active=False)
            elif scenario in {
                "bad_source_signature",
                "corpus_mismatch",
                "index_mismatch",
                "bad_source_hash",
                "expired_source",
            }:

                def transform(value):
                    if scenario == "bad_source_signature":
                        value["signature"] = "0" * 64
                        return value
                    if scenario == "corpus_mismatch":
                        value["release"]["corpus_sha256"] = "d" * 64
                    elif scenario == "index_mismatch":
                        value["release"]["index_sha256"] = "j" * 64
                    elif scenario == "bad_source_hash":
                        value["snippets"][0]["text_sha256"] = "0" * 64
                    else:
                        value["expires_at"] = int(time.time()) - 1
                    value["signature"] = hmac.new(
                        e["response_key"].encode(),
                        canonical({k: v for k, v in value.items() if k != "signature"}),
                        "sha256",
                    ).hexdigest()
                    return value

                e["knowledge_app"].state.response_transform = transform
            elif scenario == "revoked_release":
                e["release"]["active"] = False
            elif scenario == "revoked_during_generation":

                async def revoke():
                    e["release"].update(active=False, generation=2)

                e["agent_app"].state.before_publish = revoke
            elif scenario == "prescription_task_rejected":
                body["task"] = "change_prescription"
            elif scenario == "combined_sources_separate":
                body["task"] = "combined"

        response = await client.post(
            url,
            json=body,
            headers={"Authorization": header if header is not None else e["token"](claims)},
        )
        initial_code = response.status_code
        value = response.json()
        if initial_code == 200 and "run_id" in value:
            run_id = value["run_id"]
            run_url = e["agent_url"] + "/v1/preproduction/runs/" + run_id
            headers = {"Authorization": e["token"](claims)}
            if scenario == "restart_persistence":
                fresh_app = e["agent_factory"]()
                fresh_url = await e["stack"].enter_async_context(serve(fresh_app))
                response = await client.get(
                    fresh_url + "/v1/preproduction/runs/" + run_id, headers=headers
                )
                value = response.json()
                assertions["fresh_app_reads_persisted_run"] = (
                    value.get("patient", {}).get("patient") == "syn-p-a"
                )
            elif scenario in {
                "revoked_before_replay",
                "release_revoked_before_replay",
                "release_revoked_before_citation",
            }:
                if scenario == "revoked_before_replay":
                    e["state"].grant("syn-tenant", "syn-self", "syn-p-a", active=False)
                else:
                    e["release"].update(active=False, generation=2)
                target = run_url + (
                    "/citation?ref=syn-paper%40v1%23outcome"
                    if scenario == "release_revoked_before_citation"
                    else "/events"
                )
                response = await client.get(target, headers=headers)
                value = response.json()
                assertions["no_replayed_result"] = (
                    "patient" not in value and "snippets" not in value
                )
            elif scenario == "revoked_during_sse":
                entered, resume = asyncio.Event(), asyncio.Event()

                async def gate():
                    entered.set()
                    await resume.wait()

                e["agent_app"].state.stream_gate = gate
                async with client.stream("GET", run_url + "/events", headers=headers) as stream:
                    lines = stream.aiter_lines()
                    first = await anext(lines)
                    await asyncio.wait_for(entered.wait(), 3)
                    e["state"].grant("syn-tenant", "syn-self", "syn-p-a", active=False)
                    resume.set()
                    rest = "\n".join([line async for line in lines])
                    assertions["accepted_precedes_revocation"] = first == "event: accepted"
                    assertions["revocation_blocks_remaining_stream"] = (
                        "event: failure" in rest
                        and "event: result" not in rest
                        and "0.93" not in rest
                    )

        assertions["expected_http"] = response.status_code == case["expected_http"]
        if response.status_code != 200:
            assertions["no_fact_or_source_in_error"] = not any(
                k in value for k in ["patient", "literature", "measurements", "snippets"]
            )
        if case["group"] == "facts" and response.status_code == 200:
            patient = value["patient"]
            projected = patient["measurements"]
            if scenario == "no_measurement":
                assertions["missing_is_explicit"] = (
                    patient["absence"] == "no_record" and not projected
                )
            else:
                expected_state = {
                    "missing_value": "missing_value",
                    "null_value": "missing_value",
                    "placeholder_zero": "placeholder",
                    "missing_unit": "unit_unverified",
                    "unknown_unit": "unit_unverified",
                    "stale_measurement": "stale",
                    "future_measurement": "future",
                    "undated_measurement": "date_unverified",
                    "uncertain_measurement": "uncertain",
                }.get(scenario, "recorded")
                assertions["state_matches_record"] = projected[0]["state"] == expected_state
                assertions["value_not_invented"] = projected[0]["value"] == (
                    m["value"] if expected_state == "recorded" else None
                )
                assertions["right_patient"] = patient["patient"] == "syn-p-a"
        if scenario in {"self_access", "assigned_clinician"}:
            assertions["positive_record_completed"] = (
                value.get("patient", {}).get("measurements", [{}])[0].get("value") == 0.42
            )
        if scenario in {
            "approved_concept",
            "literature_not_patient_fact",
            "combined_sources_separate",
        }:
            assertions["positive_literature_completed"] = bool(
                value.get("literature", {}).get("snippets")
            )
            assertions["no_generated_claims_published"] = value["generated_answer"] is None
            assertions["patient_separate_from_literature"] = (
                value["patient"] is None
                if scenario != "combined_sources_separate"
                else value["patient"]["measurements"][0]["value"] == 0.42
            )
        outbound = e["agent_app"].state.egress
        assertions["minimal_generation_payload"] = all(
            p["question"] in CONCEPTS.values()
            and set(p) == {"question", "sources", "purpose"}
            and p["purpose"] == "literature_only"
            for p in outbound
        )
        serialized = json.dumps(outbound, ensure_ascii=False)
        assertions["no_patient_identifiers_or_values_in_generation"] = all(
            canary not in serialized
            for canary in ["syn-p-a", "syn-self", "合成同名患者", "0.42", "0.77", "+86-000"]
        )
        if case["group"] in {"identity", "facts"}:
            assertions["no_generation_or_retrieval_for_patient_record"] = (
                not outbound and not e["knowledge_app"].state.outbound
            )
        if scenario in {
            "unknown_concept",
            "free_query_rejected",
            "privacy_canary_rejected",
            "prescription_task_rejected",
        }:
            assertions["rejected_before_outbound"] = (
                not outbound and not e["knowledge_app"].state.outbound
            )
        return dict(
            **case,
            actual_http=response.status_code,
            initial_http=initial_code,
            assertions=assertions,
            passed=all(assertions.values()),
            patient_calls=len(e["patient_app"].state.calls),
            retrieval_calls=len(e["knowledge_app"].state.outbound),
            generation_calls=len(outbound),
            generation_payload_hashes=[hashlib.sha256(canonical(p)).hexdigest() for p in outbound],
            clinical_approval=False,
        )


async def main(args):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(args.meta_repo / "src"))
    suite = ROOT / "eval/synthetic_preproduction_v1"
    seal = json.loads((suite / "seal.json").read_bytes())
    raw = (suite / "cases.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == seal["cases_sha256"]
    if args.output.exists():
        raise ValueError("Preserve all attempts")
    args.output.mkdir(parents=True)
    report = dict(
        status="RUNNING",
        execution="real owned TCP HTTP; synthetic records/sources; controlled generator stub",
        seal=seal,
        rows=[],
        clinical_approval=False,
        independent_review="NOT_ASSESSED",
    )

    def save():
        (args.output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )

    save()
    for case in [json.loads(line) for line in raw.splitlines()]:
        try:
            row = await run_case(case, args.output / (case["case_id"] + ".sqlite"))
        except Exception as exc:
            row = dict(**case, passed=False, error_type=type(exc).__name__)
        report["rows"].append(row)
        save()
        print(case["case_id"], row["passed"], flush=True)
    report["status"] = (
        "PASS" if len(report["rows"]) == 60 and all(r["passed"] for r in report["rows"]) else "FAIL"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    save()
    if report["status"] != "PASS":
        raise ValueError("Frozen contract suite failed; inspect retained attempt")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--meta-repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
