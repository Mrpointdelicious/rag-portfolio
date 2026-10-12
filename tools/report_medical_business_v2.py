"""Report observable engineering outcomes; prepare complete private semantic review records."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from eval_public_medical import evidence_covered
from report_medical_business import visible_spans

from rag_portfolio.public_medical import canonical

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/medical_business_v2"
OUT = ROOT / "artifacts/medical-business-v2"


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def summarize(path):
    run = load(path)
    rows = run["cases"]
    selected_anchors = None
    if path.parent.parent.name == "agent":
        corpus = load(
            ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json"
        )
        chunks = {c["doc_ref"]: c for c in corpus["chunks"]}
        gold = {
            c["case_id"]: c
            for c in [
                json.loads(line)
                for line in (LOCAL / "gold" / f"{run['split']}.jsonl").read_bytes().splitlines()
            ]
        }
        returned = visible = total = 0
        misses = []
        for r in rows:
            if r["turn"] != 1:
                continue
            spans = [
                chunks[s["doc_ref"]]
                for e in r["valid_conversation_evidence"]
                for s in e["payload"].get("snippets", [])
                if s["doc_ref"] in chunks
            ]
            valid = {s["doc_ref"] for s in spans}
            shown = visible_spans(r, chunks, valid)
            for a in gold[r["case_id"]]["anchors"]:
                total += 1
                returned += evidence_covered({"acceptable": [a]}, spans)
                visible += evidence_covered({"acceptable": [a]}, shown)
                if not evidence_covered({"acceptable": [a]}, shown):
                    misses.append(r["case_id"])
        selected_anchors = dict(
            assignments=total,
            returned=returned,
            final_model_visible=visible,
            selected_anchor_misses=misses,
            meaning="selected span coverage only; not all task dimensions or semantic support",
        )
    return dict(
        run=path.parent.name,
        turns=len(rows),
        status=run["status"],
        nonempty_answers=sum(bool(r["answer"]) for r in rows),
        empty_cases=[f"{r['case_id']}:t{r['turn']}" for r in rows if not r["answer"]],
        model_calls=sum(len(r["model_outputs"]) for r in rows),
        tool_calls=sum(len(r["tool_results"]) for r in rows),
        runtime_status_counts=dict(Counter(r["status"] for r in rows)),
        invalid_reference_publications=sum(
            bool(r["answer"])
            and bool(r["metrics"].get("knowledge_citation_audit", {}).get("invalid_refs"))
            and r["metrics"].get("knowledge_recovery", {}).get("final_audit")
            != "fallback_without_claims"
            for r in rows
        ),
        recoveries=[
            dict(case_id=r["case_id"], turn=r["turn"], **r["metrics"]["knowledge_recovery"])
            for r in rows
            if "knowledge_recovery" in r["metrics"]
        ],
        failure_reasons=dict(
            Counter(
                x["outputs"].get("knowledge_state", {}).get("reason", "unprojected")
                for r in rows
                for x in r["tool_results"]
                if x["status"] not in ["succeeded", "partial"]
            )
        ),
        source_kinds=dict(
            Counter(
                x["outputs"].get("stages", {}).get("source_kind", "unknown")
                for r in rows
                for x in r["tool_results"]
                if x["status"] in ["succeeded", "partial"]
            )
        ),
        elapsed_ms=sum(r["elapsed_ms"] for r in rows),
        raw_sha256=sha(path.read_bytes()),
        semantic="NOT_ASSESSED",
        selected_anchors=selected_anchors,
    )


def review_bundle():
    corpus = load(ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json")
    chunks = {c["doc_ref"]: c for c in corpus["chunks"]}
    doc_sources = {d["document_id"]: d["source_sha256"] for d in corpus["documents"]}
    prior = load(ROOT / "artifacts/medical-business-v1/technical_review.json")["rows"]
    prior_map = {(r["split"], r["case_id"], r["turn"]): r for r in prior}
    records = []
    sources = [
        ("v1", split, ROOT / f".local/medical_business_v1/agent/{split}-run-1/cases.json")
        for split in ["dev", "heldout"]
    ]
    sources += [("v2", p.parent.name, p) for p in sorted((LOCAL / "agent").glob("*/cases.json"))]
    for version, split, path in sources:
        run = load(path)
        for r in run["cases"]:
            valid = {
                s["doc_ref"]
                for e in r["valid_conversation_evidence"]
                for s in e["payload"].get("snippets", [])
            }
            spans = visible_spans(r, chunks, valid)
            for span in spans:
                span["source_sha256"] = doc_sources[span["document_id"]]
            statements = [
                s.strip() for s in re.split(r"(?<=[。！？])|\n+", r["answer"]) if s.strip()
            ]
            # Every unit, including extra prose, gets an explicit pending label. A citation
            # link is a candidate span for review, never an automated entailment judgment.
            units = [
                dict(
                    unit=i,
                    text=s,
                    semantic_support="NOT_ASSESSED",
                    candidate_visible_spans=[
                        dict(
                            doc_ref=x["doc_ref"],
                            segment_id=x["segment_id"],
                            start=x["start"],
                            end=x["end"],
                            text=x["text"],
                            source_sha256=x.get("source_sha256"),
                        )
                        for x in spans
                        if x["doc_ref"] in s
                    ],
                )
                for i, s in enumerate(statements, 1)
            ]
            records.append(
                dict(
                    version=version,
                    split=split,
                    case_id=r["case_id"],
                    turn=r["turn"],
                    question=r["question"],
                    answer=r["answer"],
                    answer_sha256=sha(r["answer"].encode()),
                    raw_run_sha256=sha(path.read_bytes()),
                    raw_run=str(path),
                    prior_selected_annotation=prior_map.get((split, r["case_id"], r["turn"]))
                    if version == "v1"
                    else None,
                    requested_dimensions="NOT_ASSESSED",
                    completion="NOT_ASSESSED",
                    improper_refusal="NOT_ASSESSED",
                    reason_consistency="NOT_ASSESSED",
                    units=units,
                    all_valid_model_visible_spans=spans,
                    independent_reviewer=None,
                    clinical_approval=False,
                )
            )
    save(LOCAL / "review/full-answer-records.json", records)
    v1 = [r for r in records if r["version"] == "v1"]
    assert len(v1) == 45
    return dict(
        v1_turns=len(v1),
        v1_units=sum(len(r["units"]) for r in v1),
        prior_selected_annotations_linked=sum(
            r["prior_selected_annotation"] is not None for r in v1
        ),
        all_turns=len(records),
        all_units=sum(len(r["units"]) for r in records),
        independent_review="NOT_ASSESSED",
        full_text_location="private local review/full-answer-records.json",
        clinical_approval=False,
    )


def main():
    runs = [summarize(p) for p in sorted((LOCAL / "agent").glob("*/cases.json"))]
    paired = {}
    for split in ["dev", "heldout"]:
        reps = range(1, 4) if split == "dev" else [1]
        pairs = []
        for n in reps:
            baseline = load(LOCAL / f"agent/{split}-baseline-{n}/cases.json")["cases"]
            candidate = load(LOCAL / f"agent/{split}-C-{n}/cases.json")["cases"]
            assert [(r["case_id"], r["turn"]) for r in baseline] == [
                (r["case_id"], r["turn"]) for r in candidate
            ]
            for a, b in zip(baseline, candidate, strict=True):
                pairs.append(
                    dict(
                        case_id=a["case_id"],
                        turn=a["turn"],
                        repeat=n,
                        baseline_nonempty=bool(a["answer"]),
                        candidate_nonempty=bool(b["answer"]),
                        structural_nonempty_change="win"
                        if b["answer"] and not a["answer"]
                        else "loss"
                        if a["answer"] and not b["answer"]
                        else "tie",
                        semantic_task_outcome="NOT_ASSESSED",
                    )
                )
        paired[split] = dict(
            turn_pairs=len(pairs),
            nonempty_changes=dict(Counter(p["structural_nonempty_change"] for p in pairs)),
            pairs=pairs,
        )
    regressions = {}
    for split in ["dev", "heldout"]:
        p = LOCAL / f"regressions/business-v1-{split}-1/cases.json"
        regressions[f"business_v1_{split}"] = summarize(p)
    old = load(LOCAL / "regressions/original50-run-1/results.json")
    regressions["original50"] = dict(
        turns=len(old["rows"]),
        backlog_tested=old["backlog_tested_n"],
        splits={
            split: dict(
                total=sum(r["original_split"] == split for r in old["rows"]),
                answerable=sum(
                    r["original_split"] == split and r["scores"]["answerable"] for r in old["rows"]
                ),
                complete=sum(
                    r["original_split"] == split and r["scores"]["complete_coverage_at5"]
                    for r in old["rows"]
                ),
                budget_complete=sum(
                    r["original_split"] == split and r["scores"]["budget_complete_coverage"]
                    for r in old["rows"]
                ),
            )
            for split in ["dev", "heldout"]
        },
    )
    contract = load(LOCAL / "regressions/contracts-run-2/results.json")
    review = load(LOCAL / "regressions/contract-review-2.json")
    reviewed = next(iter(review["reports"].values()))
    regressions["contracts"] = {
        g: dict(
            turns=sum(r["case_id"].startswith(g) for r in contract["rows"]),
            original_smoke_pass=sum(
                r["case_id"].startswith(g) and r["automated"]["passed"] for r in contract["rows"]
            ),
            route_corrected_smoke_pass=sum(
                r["case_id"].startswith(g) and r["route_corrected_smoke"] for r in reviewed["rows"]
            ),
        )
        for g in ["C", "B"]
    }
    remote = load(LOCAL / "regressions/E-run-1/cases.json")
    regressions["E"] = [
        dict(
            case=r["case"],
            status=r["status"],
            model_calls=len(r["model_outputs"]),
            returned_refs=sorted(
                {s["doc_ref"] for e in r["evidence"] for s in e["payload"]["snippets"]}
            ),
            semantic="NOT_ASSESSED",
        )
        for r in remote["cases"]
    ]
    summary = dict(
        schema_version="medical-business-v2-results",
        engineering="PENDING_delivery_audits",
        business="PARTIAL",
        promotion="NOT_PROMOTED; v1 retained; C remains an experimental internal pilot",
        clinical_approval=False,
        seal=load(ROOT / "eval/medical_business_v2/seal.json"),
        runs=runs,
        paired=paired,
        regressions=regressions,
        review=review_bundle(),
        pilot=load(LOCAL / "pilot-uat-4/result.json"),
        full_suites=load(LOCAL / "full-suite-4/result.json"),
        followup_issues=dict(
            original_12=dict(
                status="deferred_known_failure",
                targeted_fix_requested=False,
                regression="all 50 run; all 12 retained and still incomplete",
                case_ids=[
                    r["case_id"]
                    for r in load(ROOT / "eval/public_medical_corpus_v1/regression_backlog.json")[
                        "cases"
                    ]
                ],
            ),
            v2_no_source_refusal=dict(
                status="open_next_version",
                observation="Both baseline and C heldout refusal ended empty after citation audit",
                action="Do not tune this candidate using opened heldout; freeze a new protocol",
            ),
            E09=dict(status="open", retrieval_runs=1, clarification_without_retrieval_runs=2),
            B07=dict(status="open", contract="tool_dependency_alternative"),
            semantic_review=dict(status="NOT_ASSESSED", independent_review_required=True),
            browser_human_uat=dict(status="NOT_ASSESSED"),
            real_patient_production=dict(
                status="NOT_READY",
                next="Trusted identity, model egress control, synthetic patient preproduction",
            ),
        ),
        limitations=[
            "Nonempty answer and valid citation membership are not semantic task completion.",
            "No unseen-source generalization; four exact anchors overlap v1.",
            "No independent semantic or clinical review.",
            "Heldout no-source refusal still blocked by current required-citation rule.",
            "Recovery only identifies citations backed by server-retained expired hints.",
            "UI verified through HTTP; independent browser/user UAT pending.",
            "Pilot startup schema and source URL failures retained and corrected. "
            "No production services changed.",
            "Provider runs overlap; latency is descriptive, not a controlled improvement estimate.",
        ],
    )
    audit_names = [
        "rag-package-audit-final.json",
        "delivery-audit-final.json",
        "runtime-source-audit.json",
    ]
    audits = {
        name: load(OUT / name) if (OUT / name).exists() else {"status": "NOT_RUN"}
        for name in audit_names
    }
    summary["delivery_audits"] = audits
    if (
        all(a["status"] == "PASS" for a in audits.values())
        and summary["pilot"]["status"] == "PASS_technical_human_UAT_pending"
        and summary["full_suites"]["cleanup_complete"]
        and all(s["exit"] == 0 for s in summary["full_suites"]["steps"])
    ):
        summary["engineering"] = "PASS"
    save(OUT / "summary.json", summary)
    print(
        json.dumps(
            dict(
                runs=len(runs),
                turns=sum(r["turns"] for r in runs),
                paired={k: v["nonempty_changes"] for k, v in paired.items()},
                review=summary["review"],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
