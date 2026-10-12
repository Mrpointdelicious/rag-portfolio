"""Publish metadata-only v4 results and retain complete synthetic review traces locally."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/project_identity_v4"


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main(args):
    model_path = LOCAL / "v3-regression/model-outputs.jsonl"
    messages = [json.loads(row) for row in model_path.read_bytes().splitlines()]
    review = dict(
        schema="project-identity-v4-private-review",
        independent_review="NOT_ASSESSED",
        clinical_approval=False,
        actual_supplier_requests=0,
        project_record_model_calls=0,
        records=[
            dict(
                record_id=f"controlled-regression-{i}",
                full_generated_output=message,
                independent_review="NOT_ASSESSED",
            )
            for i, message in enumerate(messages, 1)
            if message.get("content")
        ],
        complete_http_records={
            name: load(LOCAL / name / "private-http-review.json")
            for name in ["contracts-run-3", "v3-regression"]
        },
    )
    review_path = LOCAL / "review/full-answer-records.json"
    save(review_path, review)
    if args.prepare_review:
        print(dict(private_review_records=len(review["records"]), status="NOT_ASSESSED"))
        return
    contract = load(LOCAL / "contracts-run-3/result.json")
    regression = load(LOCAL / "v3-regression/result.json")
    suite = load(LOCAL / "full-suite-final/result.json")
    audit = load(LOCAL / args.audit / "result.json")
    counts = {}
    for name in ["rag", "meta"]:
        log = (LOCAL / f"full-suite-final/{name}-isolated-suite.log").read_text(encoding="utf8")
        counts[name] = int(re.search(r"(\d+) passed", log)[1])
    assert contract["status"] == regression["status"] == audit["status"] == "PASS"
    assert contract["passed"] == 56 and regression["passed"] == 41
    assert suite["cleanup_complete"] and all(step["exit"] == 0 for step in suite["steps"])
    summary = dict(
        schema="project-identity-v4-results",
        engineering="PASS",
        business="PARTIAL",
        real_patient="NOT_READY",
        promotion="NOT_PROMOTED",
        synthetic_only=True,
        clinical_approval=False,
        actual_supplier_requests=0,
        production_deployed=False,
        existing_patient_database_contacted=False,
        existing_login_key_loaded=False,
        source_data_rows_copied=0,
        policy=dict(
            data_generation="project-20260915",
            patient_id="project.dbuser.Id",
            account_status=1,
            membership_enable=0,
            role_enable=0,
            doctor_relation="same-tenant open consultation created within 7 days",
            relation_window_is_engineering_policy=True,
            name_phone_oldid_fallback=False,
            plan_only_grant=False,
            observed_revocation_monotonic=True,
            all_transient_revocations_detected=False,
        ),
        contract=dict(
            total=56,
            passed=56,
            cases=sorted(contract["cases"], key=lambda r: r["id"]),
            cleanup_complete=contract["cleanup_complete"],
            seal=load(ROOT / "eval/project_identity_v4/seal.json"),
            addendum_seal=load(ROOT / "eval/project_identity_v4/addendum-seal.json"),
            schema_seal=load(ROOT / "eval/project_identity_v4/schema-seal.json"),
        ),
        attempts={
            p.parent.name: dict(
                status=load(p)["status"],
                passed=load(p)["passed"],
                expected=len(load(p)["cases"]) + len(load(p).get("missing_cases", [])),
            )
            for p in sorted(LOCAL.glob("contracts-run-*/result.json"))
        },
        audit_attempts={
            p.parent.name: load(p)["status"]
            for p in sorted(LOCAL.glob("delivery-audit-*/result.json"))
        },
        regressions=dict(shared_authority_v3=dict(passed=41, total=41), full_suite=counts),
        linked_dotnet=dict(
            controller_source="actual_AI_WebApi_source",
            business_repository="synthetic_fixture",
            actual_production_host_tested=False,
        ),
        new_review=dict(
            records=len(review["records"]),
            controlled_regression=True,
            project_record_model_calls=0,
            independent_review="NOT_ASSESSED",
            private_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),
        ),
        preserved_issues=[
            "original 12 incomplete queries",
            "E09",
            "B07",
            "heldout anchor misses",
            "historical independent review queues",
        ],
        remaining_production_gates=[
            "Real backend per-route authorization and tenant/record ownership SQL",
            "Durable authorization lifecycle and monotonic revocation events",
            "Unique login session identity and logout revocation",
            "Verified current database and migrated report-to-patient provenance",
            "Managed credentials, TLS, actual .NET host and dependency remediation",
            "Capacity and independent clinical review",
        ],
    )
    save(ROOT / "artifacts/project-identity-v4/summary.json", summary)
    save(ROOT / "artifacts/project-identity-v4/delivery-audit.json", audit)
    print(
        dict(
            engineering="PASS",
            contract="56/56",
            regression="41/41",
            full_suite=counts,
            real_patient="NOT_READY",
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-review", action="store_true")
    parser.add_argument("--audit", default="delivery-audit-final")
    main(parser.parse_args())
