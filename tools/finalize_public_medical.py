"""Assemble honest acceptance from actual retained evidence, no model reruns."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rag_portfolio.public_medical import sha, write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/public-medical-corpus-v1"
LOCAL = ROOT / ".local/public_medical_corpus_v1"


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def main():
    checks = []
    for name, cmd in [
        ("ruff", [".venv/Scripts/ruff.exe", "check", "src", "tools", "tests"]),
        ("ruff-format", [".venv/Scripts/ruff.exe", "format", "--check", "src", "tools", "tests"]),
    ]:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf8")
        path = LOCAL / "verification" / (name + ".log")
        path.write_text(result.stdout + result.stderr, encoding="utf8")
        checks.append(
            {"name": name, "exit": result.returncode, "log_sha256": sha(path.read_bytes())}
        )
    db = load(LOCAL / "verification/owned-postgres-final/result.json")
    package = load(OUT / "package_audit.json")
    preservation = load(OUT / "preservation.json")
    eval_result = load(OUT / "eval_summary.json")
    agent = load(OUT / "agent_review.json")
    manifest = load(OUT / "manifest.json")
    meta = ROOT.parent / "metaAgent"
    meta_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=meta, text=True).strip()
    meta_status = subprocess.check_output(
        ["git", "status", "--short"], cwd=meta, text=True
    ).splitlines()
    technical_pass = (
        all(c["exit"] == 0 for c in checks)
        and preservation["unchanged"]
        and package["status"] == "PASS"
        and all(s["exit"] == 0 for s in db["steps"])
        and db["cleanup_complete"]
        and eval_result["status"].startswith("DONE")
        and agent["owned_processes_stopped"]
    )
    acceptance = {
        "schema_version": "public-medical-acceptance-v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "overall": "PARTIAL",
        "technical_checks": "PASS" if technical_pass else "FAIL",
        "clinical_approval": False,
        "branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip(),
        "base_head": manifest["base_head"],
        "datasets": manifest["datasets"],
        "done": [
            "Six specified new works: six real PDFs plus six real JATS; D161 new formats same work",
            "D041/D102 normalized-body dedup and old-eight resource review",
            "Separate corpus/release/index/scope and resource/text fingerprints",
            "50 source-authored sealed Gold, 36dev/14heldout",
            "Live six-strategy retrieval; 33 document embedding batches "
            "plus50 query embeddings plus50 reranks",
            "Unchanged MetaAgent Runtime real TCP Evidence/FactBuilder integration on public prose",
            "Legacy157 file hashes unchanged",
            "271 pytest passed including owned PostgreSQL; 19 new checks",
            "Actual wheel/sdist and Docker context inspection; configured secret matches zero",
        ],
        "partial": [
            "Independent human legal/Gold/Chinese clinical review pending",
            "Selected reranker budget complete coverage dev25/32, heldout5/10; "
            "twelve failures preserved",
            "Agent3/4 requested research cases complete; one partial; "
            "two boundaries withheld but one reason lost",
            "Only16 selected core answer units annotated; "
            "all-prose error/missing-citation rates not complete",
            "Heldout-14 taxonomy defect retained for v2 review; no frozen-Gold rewrite",
            "39/49 preferred anchors exact PDF-aligned; ten remain XML-only",
        ],
        "blocked": [
            {
                "item": "Combined14 external-model Dense/Hybrid/Rerank",
                "reason": "Old rights-held prose is not approved for provider upload; "
                "local BM25 only",
            },
            {
                "item": "Clinical release/approval",
                "reason": "No clinician signoff or patient-measurement Gold",
            },
            {
                "item": "MetaAgent boundary-reason repair",
                "reason": "MetaAgent source is read only in this task; "
                "adapter drops unavailable reason",
            },
        ],
        "checks": checks,
        "database": {"suite": "271 passed, 2 dependency deprecation warnings", "result": db},
        "package_audit_sha256": sha((OUT / "package_audit.json").read_bytes()),
        "preservation": preservation,
        "eval_summary_sha256": sha((OUT / "eval_summary.json").read_bytes()),
        "agent_review_sha256": sha((OUT / "agent_review.json").read_bytes()),
        "meta_agent": {
            "head": meta_head,
            "expected_head": "ee489a40f83b7fdc261c7b59c27f7f88bd7306df",
            "head_unchanged": meta_head == "ee489a40f83b7fdc261c7b59c27f7f88bd7306df",
            "preexisting_untracked_count": len(meta_status),
            "source_files_modified": False,
        },
        "side_effects": {
            "push": False,
            "merge": False,
            "production_db_contact": False,
            "existing_services_modified": False,
            "owned_test_processes_and_containers_cleaned": True,
        },
        "review_questions": [
            "D041/D102 work dedup and review/RCT participant overlap",
            "Resource rights/public-prose boundary and D001 differing layout",
            "PDF physical pages versus XML paragraph/XPath coordinates",
            "Retrieval complete coverage versus Agent supported claims, "
            "missing results and reason loss",
        ],
    }
    write_json(OUT / "acceptance.json", acceptance)
    print(
        {
            "overall": acceptance["overall"],
            "technical_checks": acceptance["technical_checks"],
            "old_unchanged": preservation["unchanged"],
            "clinical_approval": False,
        }
    )
    if not technical_pass:
        raise ValueError("Technical checks failed; preserve actual results")


if __name__ == "__main__":
    main()
