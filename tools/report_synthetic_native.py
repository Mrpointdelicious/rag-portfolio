"""Publish metadata-only native integration results; keep full semantic review private."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from eval_public_medical import evidence_covered
from report_medical_business import visible_spans
from report_medical_business_v2 import summarize

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/synthetic_native_v2"
OUT = ROOT / "artifacts/synthetic-native-v2"


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    contracts = load(LOCAL / "contracts-run-3/result.json")
    old = load(LOCAL / "v1-security-regression/result.json")
    live = {p.parent.name: load(p) for p in sorted(LOCAL.glob("live-run-*/result.json"))}
    regression = LOCAL / "regressions"
    medical = {p.parent.name: summarize(p) for p in sorted(regression.glob("v*/cases.json"))}
    corpus = load(ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json")
    chunks = {c["doc_ref"]: c for c in corpus["chunks"]}
    for split, folder in [("dev", "v2-dev-run-1"), ("heldout", "v2-held-run-1")]:
        gold = {
            c["case_id"]: c
            for c in [
                json.loads(x)
                for x in (ROOT / f".local/medical_business_v2/gold/{split}.jsonl")
                .read_bytes()
                .splitlines()
            ]
        }
        counts = Counter()
        misses = []
        for row in load(regression / folder / "cases.json")["cases"]:
            if row["turn"] != 1:
                continue
            spans = [
                chunks[s["doc_ref"]]
                for e in row["valid_conversation_evidence"]
                for s in e["payload"].get("snippets", [])
                if s["doc_ref"] in chunks
            ]
            shown = visible_spans(row, chunks, {s["doc_ref"] for s in spans})
            for a in gold[row["case_id"]]["anchors"]:
                counts["assignments"] += 1
                counts["returned"] += evidence_covered({"acceptable": [a]}, spans)
                hit = evidence_covered({"acceptable": [a]}, shown)
                counts["model_visible"] += hit
                if not hit:
                    misses.append(row["case_id"])
        medical[folder]["selected_anchors"] = {
            **counts,
            "misses": misses,
            "meaning": "Selected span coverage, not full task coverage or semantic support",
        }
    original = load(regression / "original50-run-1/results.json")
    cb = load(regression / "contracts-run-3/results.json")
    reviewed = next(iter(load(regression / "contract-review.json")["reports"].values()))
    e = load(regression / "E-run-1/cases.json")
    cb_summary = {
        group: dict(
            turns=sum(r["case_id"].startswith(group) for r in cb["rows"]),
            original_pass=sum(
                r["case_id"].startswith(group) and r["automated"]["passed"] for r in cb["rows"]
            ),
            existing_errata_pass=sum(
                r["case_id"].startswith(group) and r["route_corrected_smoke"]
                for r in reviewed["rows"]
            ),
        )
        for group in ["C", "B"]
    }
    e_summary = [
        dict(
            case=r["case"],
            tools=len(r["tool_results"]),
            nonempty=bool(r["answer"]),
            model_calls=len(r["model_outputs"]),
        )
        for r in e["cases"]
    ]
    records = []
    sources = [
        (str(p.relative_to(LOCAL)), load(p)["cases"])
        for p in sorted(regression.glob("v*/cases.json"))
    ]
    sources += [("contracts", cb["rows"]), ("E02_E09", e["cases"])]
    sources += [
        (
            name,
            [
                {"case_id": "native-live", "answer": g["text"]}
                for g in load(LOCAL / name / "private-model-review.json")["generated"]
            ],
        )
        for name in live
    ]
    for origin, rows in sources:
        for row in rows:
            text = row["answer"]
            units = [s.strip() for s in re.split(r"(?<=[。！？])|\n+", text) if s.strip()]
            records.append(
                dict(
                    origin=origin,
                    case=row.get("case_id", row.get("case")),
                    turn=row.get("turn", 1),
                    raw_record=row,
                    semantic="NOT_ASSESSED",
                    clinical="NOT_ASSESSED",
                    independent_reviewer=None,
                    units=[
                        dict(unit=i + 1, text=s, semantic_support="NOT_ASSESSED")
                        for i, s in enumerate(units)
                    ],
                )
            )
    review_path = LOCAL / "review/full-answer-records.json"
    save(review_path, records)
    suites = load(LOCAL / "full-suite-2/result.json")
    report = dict(
        engineering=contracts["status"],
        production="NOT_READY",
        promotion="NOT_PROMOTED",
        business="PARTIAL",
        clinical="NOT_ASSESSED",
        native_cases=contracts["cases"],
        v1_security_regression=dict(status=old["status"], cases=len(old["rows"])),
        live=live,
        full_suite=dict(
            rag_passed=int(
                re.findall(
                    r"(\d+) passed",
                    (LOCAL / "full-suite-2/rag-isolated-suite.log").read_text(encoding="utf8"),
                )[-1]
            ),
            meta_passed=int(
                re.findall(
                    r"(\d+) passed",
                    (LOCAL / "full-suite-2/meta-isolated-suite.log").read_text(encoding="utf8"),
                )[-1]
            ),
            owned_cleanup=suites["steps"][-1] == {"step": "remove-owned-container", "exit": 0},
            raw_sha256=sha(LOCAL / "full-suite-2/result.json"),
        ),
        medical_regressions=medical,
        original50=dict(
            cases=len(original["rows"]),
            answerable=42,
            complete=sum(r["scores"]["complete_coverage_at5"] for r in original["rows"]),
            backlog_tested=original["backlog_tested_n"],
            backlog_still_incomplete=[
                r["case_id"]
                for r in original["rows"]
                if "backlog_status" in r and not r["scores"]["complete_coverage_at5"]
            ],
            raw_sha256=sha(regression / "original50-run-1/results.json"),
        ),
        contracts_regression=cb_summary,
        E_regression=e_summary,
        review=dict(
            new_full_records=len(records),
            new_units=sum(len(r["units"]) for r in records),
            status="NOT_ASSESSED",
            private_sha256=sha(review_path),
            prior_review_dataset_relabelled=False,
        ),
        backend_contract=load(LOCAL / "backend-contract-pins.json"),
        remaining=[
            "Shared real identity/tenant/patient authority and .NET enforcement",
            "Production DB native policy integration, process restore and revocation consistency",
            "Physical retention/deletion and network/TLS/rate limits",
            "Independent semantic/clinical acceptance",
            "Deferred original12, E09/B07 and existing297 reviews remain open",
            "New opened heldout anchor miss partial-heldout-positive; "
            "support-heldout-positive remains",
            "Current IReTour gait_speed unit unconfirmed; require trusted business mapping",
        ],
    )
    save(OUT / "summary.json", report)
    print(
        json.dumps(
            dict(
                engineering=report["engineering"],
                native_cases=len(contracts["cases"]),
                review=report["review"],
                medical={k: (v["nonempty_answers"], v["turns"]) for k, v in medical.items()},
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
