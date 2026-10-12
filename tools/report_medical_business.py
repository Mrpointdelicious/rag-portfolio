"""Separate retrieval, visible evidence, citation membership and unassessed semantics."""

import json
import re
from collections import Counter
from contextlib import suppress
from pathlib import Path

from eval_public_medical import evidence_covered

from rag_portfolio.public_medical import sha, write_json

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/medical_business_v1"
OUT = ROOT / "artifacts/medical-business-v1"
REF = re.compile(r"[a-z0-9-]+@[a-z0-9-]+#[a-z0-9-]+")


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def visible_spans(row, chunks, valid):
    spans = []

    def visit(value):
        if isinstance(value, dict):
            ref = value.get("doc_ref")
            text = value.get("value", value.get("text"))
            if ref in valid and ref in chunks and isinstance(text, str):
                chunk = chunks[ref]
                offset = chunk["text"].find(text)
                if text and offset >= 0:
                    spans.append(
                        {
                            **chunk,
                            "start": chunk["start"] + offset,
                            "end": chunk["start"] + offset + len(text),
                            "text": text,
                        }
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    if not row["model_inputs"]:
        return spans
    for message in row["model_inputs"][-1]:
        if message["type"] != "tool":
            continue
        content = message["content"]
        with suppress(ValueError, TypeError):
            visit(json.loads(content) if isinstance(content, str) else content)
    return spans


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    corpus = load(ROOT / ".local/public_medical_corpus_v1/corpora/medical_combined_v1-prose-2.json")
    chunks = {c["doc_ref"]: c for c in corpus["chunks"]}
    seal = load(ROOT / "eval/medical_business_v1/seal.json")
    summary = {
        "schema_version": "medical-business-baseline-v1",
        "clinical_approval": False,
        "independent_human_review": "pending",
        "selection": "fixed hybrid_rerank; no tuning",
        "corpus_sha256": corpus["corpus_sha256"],
        "seal": seal,
        "splits": {},
        "limitations": [
            "Coverage measures exact authored anchors, not semantic answer correctness.",
            "Gold and source/answer annotations are Codex authored; independent review pending.",
            (
                "Heldout uses new task conditions within reviewed sources, "
                "not unseen-source generalization."
            ),
            "Operator-only synthetic context; actual patient remote knowledge path not tested.",
            "Help domain is explicitly absent; help boundary tests do not validate a product FAQ.",
            (
                "Provider traffic overlapped independent runs; "
                "elapsed times are descriptive, not performance gains."
            ),
            (
                "Fault-injected cases are separate from actual provider outages "
                "and retrieval no-answer ability."
            ),
        ],
    }
    audit = []
    for split in ["dev", "heldout"]:
        gold = {c["case_id"]: c for c in rows(LOCAL / "gold" / f"{split}.jsonl")}
        direct = load(LOCAL / "retrieval" / f"{split}-run-1/results.json")
        agent = load(LOCAL / "agent" / f"{split}-run-1/cases.json")
        assert direct["status"] == agent["status"] == "DONE_execution_semantic_review_pending"
        expected = {(c["case_id"], i) for c in gold.values() for i in range(1, len(c["turns"]) + 1)}
        assert {(r["case_id"], r["turn"]) for r in agent["cases"]} == expected
        assert {(r["case_id"], r["turn"]) for r in direct["rows"]} == expected
        eligible = [
            r
            for r in direct["rows"]
            if gold[r["case_id"]]["turns"][r["turn"] - 1]["evidence_groups"]
        ]
        judged = [r for r in eligible if r["scores"] is not None]
        split_audit = []
        for row in agent["cases"]:
            turn = gold[row["case_id"]]["turns"][row["turn"] - 1]
            snippets = {
                s["doc_ref"]: s
                for e in row["valid_conversation_evidence"]
                for s in e["payload"].get("snippets", [])
            }
            valid = set(snippets)
            returned = []
            invalid = []
            for ref, snippet in snippets.items():
                if ref not in chunks or any(
                    snippet[k] != chunks[ref][k]
                    for k in [
                        "text",
                        "text_sha256",
                        "source",
                        "document_version",
                        "page",
                        "start",
                        "end",
                    ]
                ):
                    invalid.append(ref)
                else:
                    returned.append(chunks[ref])
            visible = visible_spans(row, chunks, valid)
            visible_refs = {s["doc_ref"] for s in visible}
            citations = set(REF.findall(row["answer"]))
            groups = turn["evidence_groups"]
            record = {
                "case_id": row["case_id"],
                "turn": row["turn"],
                "split": split,
                "category": row["category"],
                "runtime_status": row["status"],
                "fault": row["fault"],
                "groups_total": len(groups),
                "groups_returned": sum(evidence_covered(g, returned) for g in groups),
                "groups_visible": sum(evidence_covered(g, visible) for g in groups),
                "returned_complete": bool(groups)
                and all(evidence_covered(g, returned) for g in groups),
                "visible_complete": bool(groups)
                and all(evidence_covered(g, visible) for g in groups),
                "citation_count": len(citations),
                "invalid_source_refs": sorted(invalid),
                "citations_not_in_valid_evidence": sorted(citations - valid),
                "citations_not_in_visible_text": sorted(citations - visible_refs),
                "model_calls": len(row["model_outputs"]),
                "elapsed_ms": row["elapsed_ms"],
                "technical_semantic_review": "pending annotation; no lexical grading",
                "independent_semantic_review": "pending",
            }
            split_audit.append(record)
        answerable = [r for r in split_audit if r["groups_total"]]
        raw_traffic = load(LOCAL / "agent" / f"{split}-run-1/service-trace.json")
        categories = {}
        for cat in seal["category_counts"]:
            a = [r for r in split_audit if r["category"] == cat]
            ar = [r for r in a if r["groups_total"]]
            categories[cat] = {
                "conditions": len({r["case_id"] for r in a}),
                "turns": len(a),
                "answerable_turns": len(ar),
                "returned_complete": sum(r["returned_complete"] for r in ar),
                "visible_complete": sum(r["visible_complete"] for r in ar),
            }
        durations = sorted(r["elapsed_ms"] for r in split_audit)
        summary["splits"][split] = {
            "conditions": len(gold),
            "turns": len(expected),
            "retrieval": {
                "answerable_turns": len(eligible),
                "executed_answerable_turns": len(judged),
                "complete_at5": sum(r["scores"]["complete_coverage_at5"] for r in judged),
                "budget_complete": sum(r["scores"]["budget_complete_coverage"] for r in judged),
                "not_retrieved": dict(
                    Counter(r.get("reason") for r in direct["rows"] if r["scores"] is None)
                ),
                "model_calls": direct["model_calls"],
            },
            "agent": {
                "answerable_turns": len(answerable),
                "returned_complete": sum(r["returned_complete"] for r in answerable),
                "visible_complete": sum(r["visible_complete"] for r in answerable),
                "status_counts": dict(Counter(r["runtime_status"] for r in split_audit)),
                "model_calls": sum(r["model_calls"] for r in split_audit),
                "knowledge_requests": len(raw_traffic),
                "retrieval_model_calls": {
                    k: sum(r["model_calls"][k] for r in raw_traffic)
                    for k in ["embedding", "rerank"]
                },
                "p50_ms": durations[len(durations) // 2],
                "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))],
                "semantic_task_completion": "NOT_ASSESSED_independent_human_pending",
            },
            "categories": categories,
            "owned_processes_stopped": agent["owned_processes_stopped"],
        }
        audit.extend(split_audit)
    write_json(OUT / "eval_summary.json", summary)
    write_json(
        OUT / "agent_trace_audit.json",
        {
            "rows": audit,
            "clinical_approval": False,
            "definition": (
                "Visible anchors are verified exact source substrings in final model tool facts, "
                "not just visible citation IDs."
            ),
        },
    )
    regression = load(LOCAL / "regressions/run-1/results.json")
    baseline = load(ROOT / "artifacts/public-medical-corpus-v1/eval_summary.json")
    fixed = []
    for row in regression["rows"]:
        fixed.append({k: v for k, v in row.items() if k not in ["retrieval", "question"]})
    regsummary = {
        "tested": len(fixed),
        "backlog_tested": regression["backlog_tested_n"],
        "role": "opened regression only; no new heldout claim",
        "targeted_fixes": False,
        "splits": {},
    }
    for split in ["dev", "heldout"]:
        values = [r for r in fixed if r["original_split"] == split and r["scores"]["answerable"]]
        regsummary["splits"][split] = {
            "answerable": len(values),
            "complete_at5": sum(r["scores"]["complete_coverage_at5"] for r in values),
            "budget_complete": sum(r["scores"]["budget_complete_coverage"] for r in values),
            "baseline_complete_at5": round(
                baseline["splits"][split]["hybrid_rerank"]["complete_coverage_at5"] * len(values)
            ),
        }
    write_json(OUT / "fixed_regression_report.json", {"summary": regsummary, "rows": fixed})
    verification = load(LOCAL / "releases/verification-1.json")
    write_json(
        OUT / "release_verification.json", {k: v for k, v in verification.items() if k != "calls"}
    )
    registry = load(LOCAL / "releases/registry.json")
    write_json(
        OUT / "release_manifest.json",
        {
            "scope": "operator nonclinical isolated candidate",
            "active": registry["active"],
            "clinical_approval": False,
            "public_redistribution": False,
            "releases": [
                {k: v for k, v in entry.items() if not k.endswith("_path")}
                for entry in registry["releases"].values()
            ],
            "history": registry["history"],
            "combined": {
                "works": len(corpus["documents"]),
                "segments": len(corpus["segments"]),
                "chunks": len(corpus["chunks"]),
                "index": load(LOCAL / "index_manifest.json"),
            },
        },
    )
    fingerprints = {
        p.relative_to(ROOT).as_posix(): sha(p.read_bytes())
        for p in [
            LOCAL / "retrieval/dev-run-1/results.json",
            LOCAL / "retrieval/heldout-run-1/results.json",
            LOCAL / "agent/dev-run-1/cases.json",
            LOCAL / "agent/heldout-run-1/cases.json",
            LOCAL / "regressions/run-1/results.json",
            ROOT / "eval/medical_business_v1/seal.json",
        ]
    }
    write_json(OUT / "run_fingerprints.json", fingerprints)
    print(
        {
            "business_conditions": 40,
            "turns": len(audit),
            "regressions": len(fixed),
            "backlog_tested": regsummary["backlog_tested"],
            "independent_semantic_review": "pending",
        }
    )


if __name__ == "__main__":
    main()
