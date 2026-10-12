"""Publish audit-sized results, never raw assets, vectors, prompts or held-rights prose."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from rag_portfolio.public_medical import canonical, sha, write_json
from rag_portfolio.public_medical_retrieval import input_text
from rag_portfolio.services.retrieval_algorithms import LocalBM25Retriever

LOCAL = Path(".local/public_medical_corpus_v1")
OUT = Path("artifacts/public-medical-corpus-v1")


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


async def main():
    run = LOCAL / "runs/run-1"
    summary = load(run / "summary.json")
    all_rows = []
    failures = []
    category_metrics = {}
    for split in ["dev", "heldout"]:
        rows = [
            json.loads(line)
            for line in (run / f"{split}.jsonl").read_text(encoding="utf8").splitlines()
        ]
        all_rows.extend(rows)
        groups = {}
        for row in rows:
            groups.setdefault(row["expected_behavior"], []).append(row)
            selected = row["scores"][summary["selected_strategy"]]
            if selected["answerable"] and not selected["budget_complete_coverage"]:
                failures.append(
                    {
                        "case_id": row["case_id"],
                        "question": row["question"],
                        "groups_covered_at5": selected["groups_covered"],
                        "groups_total": selected["groups_total"],
                        "top5": row["retrieval"]["strategies"][summary["selected_strategy"]][
                            "top5"
                        ],
                        "reason": "Exact acceptable anchor group missing; "
                        "no post-heldout repair/tuning",
                    }
                )
        category_metrics[split] = {
            name: {
                "n": len(items),
                "input_gate_refused": sum(i["retrieval"]["boundary"] != "retrieve" for i in items),
                "selected_budget_complete": sum(
                    i["scores"][summary["selected_strategy"]]["budget_complete_coverage"]
                    for i in items
                ),
            }
            for name, items in groups.items()
        }
    write_json(
        OUT / "eval_summary.json",
        summary
        | {
            "category_metrics": category_metrics,
            "raw_attempts_preserved": True,
            "provider_request_counts": {
                "document_embedding_batches": len(
                    load(OUT / "index_manifest.json")["provider_calls"]
                ),
                "query_embedding": len(all_rows),
                "rerank": len(all_rows),
            },
            "metric_limits": [
                "Gold authored by Codex directly from sources; independent human pending",
                "Exact spans may undercount valid paraphrases elsewhere; "
                "preferred anchors are a subset of acceptable",
                "Heldout-14 contains a taxonomy defect: Parkinson false-premise question "
                "labelled missing_patient_data; refusal reported by sealed label, "
                "not silently corrected",
                "Input boundary rules are not a general no-answer confidence model",
                "Source IDs appear explicitly in questions; "
                "not an unprompted source-routing benchmark",
            ],
        },
    )
    write_json(
        OUT / "failure_cases.json",
        {
            "selected_strategy": summary["selected_strategy"],
            "retrieval_failures": failures,
            "gold_followup": {
                "case_id": "pmv1-heldout-14",
                "issue": "False-premise/disease mixture labelled missing_patient_data "
                "by question authoring heuristic; retain frozen Gold; "
                "independent reviewer should correct in v2",
            },
        },
    )
    write_json(OUT / "dev_selection.json", load(run / "dev-selection.json"))
    # Allow independent checksum verification without publishing large query traces.
    write_json(
        OUT / "run_fingerprints.json",
        {p.name: sha(p.read_bytes()) for p in run.iterdir() if p.is_file()},
    )
    agent = load(LOCAL / "agent/run-3/cases.json")
    cases = []
    notes = {
        "S01": (
            "source_supported",
            "8/8 requested core units supported: n38; <6 months; 17/21; 4 weeks; "
            "3+2 days; control5; equal duration; no blinding",
        ),
        "S02": (
            "partial",
            "NEW02 group conclusion supported; NEW03 own primary result not retrieved. "
            "Agent explicitly states this gap and distinguishes other studies in Discussion; "
            "requested comparison remains incomplete",
        ),
        "S03": (
            "source_supported",
            "Observed steps/aerobic minutes and R² supported; group statistics distinguished "
            "from individual prescription; source's mean10 versus conclusion<10 wording preserved",
        ),
        "S04": (
            "source_supported",
            "1110/48099 before and 341/14522 after dedup supported; "
            "refuses adding overlapping review populations; "
            "no independent-cohort count inferred",
        ),
        "N01": (
            "reason_loss",
            "Correctly withholds recommendation grade, but says no hit although service "
            "stopped before retrieval due out_of_public_scope; adapter loses the reason",
        ),
        "N02": (
            "safe_boundary",
            "Refuses filling FAC=4 without measurement; no evidence or patient lookup occurred",
        ),
    }
    for c in agent["cases"]:
        snippets = [s for e in c["evidence"] for s in e["payload"].get("snippets", [])]
        available = {s["doc_ref"] for s in snippets}
        refs = set(re.findall(r"\[([a-z0-9-]+@[a-z0-9-]+#[a-z0-9-]+)\]", c["answer"]))
        visible = canonical(c["model_inputs"][-1]).decode() if c["model_inputs"] else ""
        cases.append(
            {
                "case_id": c["case_id"],
                "question": c["question"],
                "answer": c["answer"],
                "status": c["status"],
                "evidence_count": len(c["evidence"]),
                "model_calls": len(c["model_outputs"]),
                "cited_doc_refs": sorted(refs),
                "unknown_citations": sorted(refs - available),
                "citations_visible_to_final_model": all(r in visible for r in refs),
                "source_text_hashes": {s["doc_ref"]: s["text_sha256"] for s in snippets},
                "technical_semantic_review": {
                    "reviewer": "Codex reading source spans and final answers; "
                    "independent human pending",
                    "verdict": notes[c["case_id"]][0],
                    "note": notes[c["case_id"]][1],
                    "clinical_correctness": "not assessed",
                },
            }
        )
    old_agent = load(LOCAL / "agent/run-2/cases.json")
    write_json(
        OUT / "agent_review.json",
        {k: v for k, v in agent.items() if k != "cases"}
        | {
            "cases": cases,
            "independent_human_review": "pending",
            "metrics": {
                "positive_case_n": 4,
                "fully_source_answered_requested_cases": 3,
                "partial_requested_answer": 1,
                "withheld_boundary_n": 2,
                "boundary_withheld": 2,
                "boundary_reason_lost": 1,
                "case_with_unknown_citation": sum(bool(c["unknown_citations"]) for c in cases),
                "case_with_citation_missing_from_final_input": sum(
                    not c["citations_visible_to_final_model"] for c in cases
                ),
                "fact_error_count": "not assigned as exhaustive clinical score; "
                "technical core-claim review found no contradicted numeric core claim",
                "uncited_factual_claim_count": "not exhaustively annotated; "
                "citation-ID integrity is not claim entailment",
            },
            "prior_attempts": [
                {
                    "attempt": 1,
                    "failure": "configuration rejected timeout30 > allowed15; no Agent execution",
                },
                {
                    "attempt": 2,
                    "failure": "ephemeral harness kept default help-only domain; "
                    "health refused; 6 real model cases retained",
                    "statuses": [c["status"] for c in old_agent["cases"]],
                    "model_calls": sum(len(c["model_outputs"]) for c in old_agent["cases"]),
                },
            ],
        },
    )
    current = load(LOCAL / "corpora/medical_combined_v1-prose-2.json")
    prior = load(LOCAL / "corpora/medical_combined_v1.json")
    bm25 = LocalBM25Retriever({UUID(c["chunk_id"]): input_text(c) for c in current["chunks"]})
    chunks = {c["chunk_id"]: c for c in current["chunks"]}
    checks = []
    for code, query in [
        ("D040", "D040 中国脑卒中康复治疗指南 2011 2012"),
        ("D042", "D042 中国膝骨关节炎康复治疗指南 2023 2024"),
        ("D041", "D041 脑卒中 智能 康复 共识"),
    ]:
        ranking = await bm25.search(query, limit=5)
        checks.append(
            {
                "query": query,
                "expected_work": code,
                "top5_document_ids": [chunks[str(c.chunk_id)]["document_id"] for c in ranking],
                "hit": any(chunks[str(c.chunk_id)]["document_id"] == code for c in ranking),
            }
        )
    write_json(
        OUT / "combined_index_manifest.json",
        {
            "index_version": "medical-combined-v1-bm25-2",
            "corpus_sha256": current["corpus_sha256"],
            "kind": "real local BM25; no external models",
            "chunk_count": len(current["chunks"]),
            "checks": checks,
            "dense_hybrid_rerank": "BLOCKED for rights-held old prose; "
            "public-only evaluation separate",
        },
    )
    write_json(
        OUT / "release_changes.json",
        {
            "recorded_at": datetime.now(UTC).isoformat(),
            "prior_combined_release": prior["release"],
            "prior_sha256": prior["corpus_sha256"],
            "current_combined_release": current["release"],
            "current_sha256": current["corpus_sha256"],
            "reason": "D041 author initially mistranscribed as Gu Ying; PDF physical page1 "
            "verifies Pan Yu, Zhang Hao, Xie Qing, Jiang Shan, Liu Shaxin; "
            "corrected metadata in prose-2",
            "prior_release_retained_locally": True,
            "public_corpus_gold_and_index_unchanged": True,
        },
    )
    print(
        {
            "retrieval_failure_count": len(failures),
            "agent_cases": len(cases),
            "clinical_approval": False,
        }
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
