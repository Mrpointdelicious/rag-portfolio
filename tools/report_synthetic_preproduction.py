"""Publish engineering metrics only; private sources, records and model output stay local."""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/synthetic_preproduction_v1"
OUT = ROOT / "artifacts/synthetic-preproduction-v1"


def load(path):
    return json.loads(path.read_bytes())


def main():
    contracts = load(LOCAL / "contracts-run-3/result.json")
    live = load(LOCAL / "live-run-1/result.json")
    suites = load(LOCAL / "full-suite-3/result.json")
    audit = load(OUT / "delivery-audit.json")
    summary = dict(
        schema_version="synthetic-preproduction-v1-results",
        contract_execution=contracts["execution"],
        contract_status=contracts["status"],
        seal=contracts["seal"],
        conditions=len(contracts["rows"]),
        passed=sum(r["passed"] for r in contracts["rows"]),
        groups=dict(Counter(r["group"] for r in contracts["rows"])),
        rows=contracts["rows"],
        live=live,
        full_suites=suites,
        delivery_audit=audit,
        clinical_approval=False,
        business_acceptance="NOT_ASSESSED; prior v2 PARTIAL retained",
        promotion="NOT_READY for real patients or production",
        independent_review="NOT_ASSESSED",
        limitations=[
            "Native Agent and existing AI WebAPI policy wiring remains pending.",
            "60 conditions use synthetic fixtures and a controlled generator over real HTTP.",
            "Separate live check uses real models; one smoke run cannot measure answer accuracy.",
            "Generated claims stay private for review; patient record display is deterministic.",
            "Fresh app/SQLite persistence is tested; production failover is pending.",
            "Real identity issuance, patient relationships, TLS, rate limits, retention/deletion "
            "and independent clinical review remain required.",
            "Revocation checks future publication and replay; delivered data cannot be recalled.",
            "Prior 12 retrieval failures, E09/B07 and pending semantic review remain unchanged.",
        ],
    )
    summary["engineering"] = (
        "PASS"
        if contracts["status"] == "PASS"
        and live["status"] == "PASS_engineering_semantic_pending"
        and audit["status"] == "PASS"
        and suites["cleanup_complete"]
        and all(s["exit"] == 0 for s in suites["steps"])
        else "FAIL"
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_bytes(
        json.dumps(summary, ensure_ascii=False, indent=2).encode() + b"\n"
    )
    print(
        json.dumps(
            dict(
                engineering=summary["engineering"],
                conditions=summary["conditions"],
                passed=summary["passed"],
                promotion=summary["promotion"],
            )
        )
    )


if __name__ == "__main__":
    main()
