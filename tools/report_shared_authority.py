"""Publish metadata-only shared authority evidence and preserve full synthetic review locally."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/shared_authority_v3"


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    final = LOCAL / "contracts-run-6"
    contract = load(final / "result.json")
    model = [json.loads(line) for line in (final / "model-outputs.jsonl").read_bytes().splitlines()]
    http = load(final / "private-http-review.json")
    review = dict(
        schema="shared-authority-synthetic-review-v3",
        status="NOT_ASSESSED",
        clinical_approval=False,
        controlled_model=True,
        supplier_requests=0,
        records=[
            dict(
                record_id=f"synthetic-native-v3-{i}",
                full_generated_output=message,
                independent_review="NOT_ASSESSED",
            )
            for i, message in enumerate(model, 1)
            if message.get("content")
        ],
        complete_http_records=http,
    )
    review_path = LOCAL / "review/full-answer-records.json"
    save(review_path, review)
    counts = {}
    for name in ["rag", "meta"]:
        log = (LOCAL / f"full-suite-3/{name}-isolated-suite.log").read_text(encoding="utf8")
        counts[name] = int(re.search(r"(\d+) passed", log)[1])
    regressions = {}
    for name in ["v1-security-regression", "v2-native-regression-2"]:
        data = load(LOCAL / name / "result.json")
        rows = data.get("cases", data.get("rows", []))
        regressions[name] = dict(
            total=len(rows), passed=sum(bool(r["passed"]) for r in rows), semantic="NOT_ASSESSED"
        )
    summary = dict(
        schema="shared-authority-v3-results",
        engineering=contract["status"],
        business="PARTIAL",
        promotion="NOT_PROMOTED",
        real_patient="NOT_READY",
        synthetic_only=True,
        clinical_approval=False,
        actual_supplier_requests=0,
        contract=dict(
            total=len(contract["cases"]),
            passed=contract["passed"],
            seal=load(ROOT / "eval/shared_authority_v3/seal.json"),
            addendum_seal=load(ROOT / "eval/shared_authority_v3/addendum-seal.json"),
            recovery_seal=load(ROOT / "eval/shared_authority_v3/recovery-seal.json"),
            cases=contract["cases"],
            cleanup_complete=contract["cleanup_complete"],
        ),
        attempts={
            p.parent.name: dict(
                status=load(p)["status"], passed=load(p)["passed"], total=len(load(p)["cases"])
            )
            for p in sorted(LOCAL.glob("contracts-run-*/result.json"))
        },
        regressions=regressions,
        full_suite=dict(passed=counts, result=load(LOCAL / "full-suite-3/result.json")),
        new_review=dict(
            records=len(review["records"]),
            independent_review="NOT_ASSESSED",
            controlled_model=True,
            private_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),
        ),
        preserved_issues=[
            "original_12_incomplete",
            "E09_unstable",
            "B07_independent_history_analysis_missing",
            "partial-heldout-positive_anchor_missing",
            "support-heldout-positive_anchor_missing",
        ],
        historical_review_queues=[
            dict(records=297, units=1924, status="NOT_ASSESSED"),
            dict(records=125, units=1097, status="NOT_ASSESSED"),
        ],
        limitations=[
            "synthetic server login only; no production SSO",
            "linked actual controllers with fixture services; no real repository calls",
            "net10 fixture host does not prove net6 production deployment",
            "completed snapshot restart only; no interrupted native task resume",
            "single native worker; no multi-replica claim",
            "maintenance deletion while worker stopped; no backup/WAL erasure claim",
            "no new supplier calls or clinical/business semantics acceptance",
            "current backend dependency NU1903 warning remains",
        ],
    )
    save(ROOT / "artifacts/shared-authority-v3/summary.json", summary)
    print(
        dict(
            engineering=summary["engineering"],
            conditions=contract["passed"],
            regressions=regressions,
            full_suite=counts,
            controlled_review_records=len(review["records"]),
        )
    )


if __name__ == "__main__":
    main()
