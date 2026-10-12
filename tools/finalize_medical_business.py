"""Verify retained runs and preservation; assemble acceptance without any model reruns."""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rag_portfolio.public_medical import sha, write_json

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/medical_business_v1"
OUT = ROOT / "artifacts/medical-business-v1"
META = ROOT.parent / "metaAgent"


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def fingerprint(path):
    return sha(path.read_bytes())


def preserved(snapshot):
    return [
        p
        for p, expected in snapshot["files"].items()
        if not (ROOT / p).is_file() or fingerprint(ROOT / p) != expected
    ]


def check_seal_and_anchors():
    seal = load(ROOT / "eval/medical_business_v1/seal.json")
    corpus = load(ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json")
    segments = {s["segment_id"]: s for s in corpus["segments"]}
    documents = {d["document_id"]: d for d in corpus["documents"]}
    anchors = set()
    assignments = 0
    for split in ["dev", "heldout"]:
        questions = ROOT / "eval/medical_business_v1" / f"{split}.jsonl"
        gold_path = LOCAL / "gold" / f"{split}.jsonl"
        assert fingerprint(questions) == seal[f"{split}_cases_sha256"]
        assert fingerprint(gold_path) == seal[f"{split}_gold_sha256"]
        for line in gold_path.read_bytes().splitlines():
            for turn in json.loads(line)["turns"]:
                for group in turn["evidence_groups"]:
                    assignments += len(group["acceptable"])
                    for anchor in group["acceptable"] + group["preferred"]:
                        segment = segments[anchor["segment_id"]]
                        text = segment["text"][anchor["start"] : anchor["end"]]
                        assert text == anchor["text"]
                        assert sha(text.encode()) == anchor["text_sha256"]
                        assert segment["document_id"] == anchor["document_id"]
                        assert (
                            documents[anchor["document_id"]]["source_sha256"]
                            == anchor["source_sha256"]
                        )
                        anchors.add((anchor["segment_id"], anchor["start"], anchor["end"]))
    assert assignments == 59 and len(anchors) == 45
    return {
        "questions_and_private_gold_pins": "PASS",
        "verified_anchor_assignments": assignments,
        "unique_verified_source_spans": len(anchors),
    }


def prior_regressions():
    contract_path = LOCAL / "prior-regressions/contract-run-1/results.json"
    review_path = LOCAL / "prior-regressions/contract-review-1.json"
    remote_path = LOCAL / "prior-regressions/remote-run-1/cases.json"
    contract, review, remote = [load(p) for p in [contract_path, review_path, remote_path]]
    assert contract["repeats"] == 1 and len(contract["rows"]) == 36
    reviewed = next(iter(review["reports"].values()))
    groups = {}
    for group in ["C", "B"]:
        rows = [r for r in contract["rows"] if r["case_id"].startswith(group)]
        groups[group] = {
            "turns": len(rows),
            "original_pass": sum(r["automated"]["passed"] for r in rows),
            "original_failed_cases": sorted(
                {r["case_id"] for r in rows if not r["automated"]["passed"]}
            ),
        }
    assert groups["C"]["original_failed_cases"] == ["C09", "C10"]
    assert reviewed["route_corrected_pass"] == 36
    notes = {
        "E09-1": "unfilled: asked clarification before any retrieval",
        "E09-2": "partial: planned endpoints supported; overall inability to compare too broad",
        "E09-3": "partial: planned endpoints supported; overall inability to compare too broad",
    }
    remote_rows = []
    for row in remote["cases"]:
        refs = sorted(
            {
                s["doc_ref"]
                for evidence in row["evidence"]
                for s in evidence["payload"].get("snippets", [])
            }
        )
        if row["case"].startswith("E02"):
            expected = {
                f"protocol-{name}@v1#{part}"
                for name in ["a", "b"]
                for part in ["population", "intervention", "outcomes"]
            }
            assert set(refs) == expected
            note = "necessary multi-source evidence returned; not a full semantic score"
        else:
            note = notes[row["case"]]
        remote_rows.append(
            {
                "case": row["case"],
                "runtime_status": row["status"],
                "returned_refs": refs,
                "model_calls": len(row["model_outputs"]),
                "answer_sha256": sha(row["answer"].encode()),
                "bounded_manual_technical_annotation": note,
            }
        )
    assert len(remote_rows) == 6
    return {
        "execution": "existing unchanged MetaAgent tools; real models; all attempts retained",
        "C_B": {
            "repeats": 1,
            "groups": groups,
            "existing_route_review": {k: v for k, v in reviewed.items() if k != "rows"},
            "rubric": review["rubric"],
            "not_comparable_to_historical_three_run_mean": True,
        },
        "E02_E09": {"repeats_each": 3, "overall": "PARTIAL", "rows": remote_rows},
        "clinical_approval": False,
        "independent_human_review": "pending",
        "raw_run_sha256": {
            p.relative_to(ROOT).as_posix(): fingerprint(p)
            for p in [contract_path, review_path, remote_path]
        },
    }


def main():
    verification = LOCAL / "verification"
    checks = []
    for name, cmd in [
        ("ruff", [".venv/Scripts/ruff.exe", "check", "src", "tools", "tests"]),
        ("ruff-format", [".venv/Scripts/ruff.exe", "format", "--check", "src", "tools", "tests"]),
        ("git-diff-check", ["git", "diff", "--check"]),
    ]:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf8")
        path = verification / (name + "-final.log")
        path.write_text(result.stdout + result.stderr, encoding="utf8")
        checks.append({"name": name, "exit": result.returncode, "log_sha256": fingerprint(path)})
    assert all(c["exit"] == 0 for c in checks), "See local check logs"
    legacy_path = ROOT / ".local/public_medical_corpus_v1/legacy-baseline.json"
    before_path = LOCAL / "predecessor_snapshot.json"
    legacy, before = load(legacy_path), load(before_path)
    legacy_changes, current_changes = preserved(legacy), preserved(before)
    allowed = [
        "eval/public_medical_corpus_v1/README.md",
        "eval/public_medical_corpus_v1/regression_backlog.json",
    ]
    assert not legacy_changes and sorted(current_changes) == sorted(allowed)
    old_backlog = load(LOCAL / "registered_backlog.json")
    backlog = load(ROOT / allowed[1])
    for value in [old_backlog, backlog]:
        value.pop("regression_execution")
    assert old_backlog == backlog
    assert git(META, "rev-parse", "HEAD") == before["meta_head"]
    expected_status = [
        "?? docs/acceptance/business-eval-2026-10-11/",
        "?? docs/acceptance/contract-eval-2026-10-11/",
        "?? docs/business-rag-eval-plan.md",
        "?? docs/business-rag-eval-results.md",
        "?? docs/business-rag-improvement-plan.md",
        "?? docs/contract-business-eval-results.md",
    ]
    meta_status = git(META, "status", "--porcelain=v1").splitlines()
    assert meta_status == expected_status
    preservation = {
        "legacy": {
            "files": len(legacy["files"]),
            "changed": legacy_changes,
            "snapshot_sha256": fingerprint(legacy_path),
        },
        "iteration_start": {
            "files": len(before["files"]),
            "byte_identical": len(before["files"]) - len(current_changes),
            "documented_updates": current_changes,
            "backlog_changes_limited_to_execution_registration": True,
            "snapshot_sha256": fingerprint(before_path),
        },
        "meta_head": before["meta_head"],
        "meta_status_matches_start": True,
        "untracked_preexisting_meta_paths": meta_status,
        "status": "PASS",
    }
    write_json(OUT / "preservation.json", preservation)
    write_json(OUT / "prior_regressions.json", prior_regressions())
    seal_check = check_seal_and_anchors()
    db_path = verification / "owned-postgres-final/result.json"
    db = load(db_path)
    suite_path = db_path.parent / "rag-isolated-suite.log"
    match = re.search(
        r"(\d+) passed(?:, (\d+) warnings)? in", suite_path.read_text(encoding="utf8")
    )
    assert match and int(match[1]) == 283
    assert all(s["exit"] == 0 for s in db["steps"]) and db["cleanup_complete"]
    package = load(OUT / "package_audit.json")
    assert package["status"] == "PASS" and len(package["packages"]) == 2
    summary = load(OUT / "eval_summary.json")
    audit = load(OUT / "agent_trace_audit.json")
    assert sum(s["conditions"] for s in summary["splits"].values()) == 40
    assert len(audit["rows"]) == 45
    assert all(s["owned_processes_stopped"] for s in summary["splits"].values())
    assert not any(
        r[k]
        for r in audit["rows"]
        for k in [
            "invalid_source_refs",
            "citations_not_in_valid_evidence",
            "citations_not_in_visible_text",
        ]
    )
    fixed = load(OUT / "fixed_regression_report.json")
    assert fixed["summary"]["tested"] == 50 and fixed["summary"]["backlog_tested"] == 12
    release = load(OUT / "release_verification.json")
    assert release["status"] == "PASS" and len(release["checks"]) == 7
    manifest = load(OUT / "release_manifest.json")
    assert manifest["active"] == "combined-business-v1"
    assert manifest["combined"]["works"] == 14 and manifest["combined"]["chunks"] == 713
    assert len(manifest["combined"]["index"]["provider_calls"]) == 72
    sources = [
        ROOT / "src/rag_portfolio/public_medical_retrieval.py",
        *sorted((ROOT / "src/rag_portfolio").glob("medical_release*.py")),
        *sorted((ROOT / "tools").glob("*medical_business*.py")),
        *sorted((ROOT / "tools").glob("*medical_release*.py")),
    ]
    agents = {s: load(LOCAL / "agent" / f"{s}-run-1/cases.json") for s in ["dev", "heldout"]}
    source_hashes = {
        "candidate_files": {p.relative_to(ROOT).as_posix(): fingerprint(p) for p in sources},
        "meta_agent_head": before["meta_head"],
        "meta_runtime": {
            p: fingerprint(META / p)
            for p in [
                "src/meta_agent/agent/runtime.py",
                "src/meta_agent/domains/knowledge.py",
                "src/meta_agent/domains/knowledge_citations.py",
                "src/meta_agent/prompts/templates/business_grounding.v9.md",
            ]
        },
        "run_configuration": {
            s: {
                k: a[k]
                for k in ["model", "prompt_bundle", "corpus_sha256", "index_sha256", "budget"]
            }
            for s, a in agents.items()
        },
        "fingerprint_scope": "candidate delivery files; MetaAgent pinned by unchanged Git HEAD",
    }
    write_json(OUT / "source_fingerprints.json", source_hashes)
    acceptance = {
        "schema_version": "medical-business-acceptance-v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "base_head": before["rag_head"],
        "branch": git(ROOT, "branch", "--show-current"),
        "overall": "PARTIAL",
        "technical_delivery": "PASS",
        "clinical_approval": False,
        "independent_human_review": "pending",
        "production_changed": False,
        "checks": checks,
        "sealed_dataset_check": seal_check,
        "isolated_postgres": {
            "passed": int(match[1]),
            "warnings": int(match[2] or 0),
            "cleanup_complete": db["cleanup_complete"],
            "report_sha256": fingerprint(db_path),
            "suite_log_sha256": fingerprint(suite_path),
        },
        "package_audit_sha256": fingerprint(OUT / "package_audit.json"),
        "preservation_sha256": fingerprint(OUT / "preservation.json"),
        "business_conditions": 40,
        "business_turns": 45,
        "original_regressions": 50,
        "deferred_backlog_tested": 12,
        "targeted_backlog_fixes": False,
        "open_items": [
            "Original twelve incomplete coverage cases retained without targeted fixes.",
            "Answerable bibliography question declined before retrieval.",
            "Help scope exclusion paraphrased as no retrieval hit.",
            "Expired evidence not re-fetched; invalid draft citation blocked, task unfinished.",
            "Independent Gold and full-answer semantic/clinical review pending.",
        ],
        "performance_claim": "none; latency descriptive and runs overlap provider traffic",
    }
    write_json(OUT / "acceptance.json", acceptance)
    print({"technical_delivery": "PASS", "overall": "PARTIAL", "passed": int(match[1])})


if __name__ == "__main__":
    main()
