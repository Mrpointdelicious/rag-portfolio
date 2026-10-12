"""Fixed-strategy live business baseline and opened-case regressions, never tuning."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from eval_public_medical import score

from rag_portfolio.medical_release import combined_boundary, load
from rag_portfolio.public_medical import sha, write_json
from rag_portfolio.public_medical_retrieval import PublicMedicalRetriever

LOCAL = Path(".local/medical_business_v1")
SUITE = Path("eval/medical_business_v1")


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def business_cases(split, corpus):
    seal = load(SUITE / "seal.json")
    assert seal["corpus_sha256"] == corpus["corpus_sha256"]
    for folder, key in [(SUITE, "cases"), (LOCAL / "gold", "gold")]:
        assert sha((folder / f"{split}.jsonl").read_bytes()) == seal[f"{split}_{key}_sha256"]
    cases = rows(LOCAL / "gold" / f"{split}.jsonl")
    segments = {s["segment_id"]: s for s in corpus["segments"]}
    for case in cases:
        for turn in case["turns"]:
            for group in turn["evidence_groups"]:
                for anchor in group["acceptable"]:
                    segment = segments[anchor["segment_id"]]
                    assert segment["text"][anchor["start"] : anchor["end"]] == anchor["text"]
                    assert sha(anchor["text"].encode()) == anchor["text_sha256"]
    return cases


async def main(args):
    if args.output.exists():
        raise ValueError("Preserve every run; choose a new output directory")
    corpus = load(args.corpus)
    index = load(args.index)
    approval = load(args.approval) if args.approval else None
    retriever = PublicMedicalRetriever(
        corpus, index, processing_approval=approval, route=combined_boundary if approval else None
    )
    args.output.mkdir(parents=True)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "strategy": "hybrid_rerank",
        "corpus_sha256": corpus["corpus_sha256"],
        "index_sha256": index["index_sha256"],
        "clinical_approval": False,
        "semantic_review": "pending independent human",
        "status": "running",
        "rows": [],
        "model_calls": {"embedding": 0, "rerank": 0},
    }
    try:
        if args.mode == "regressions":
            folder = Path("eval/public_medical_corpus_v1")
            seal = load(folder / "seal.json")
            assert seal["corpus_sha256"] == corpus["corpus_sha256"]
            cases = []
            for split in ["dev", "heldout"]:
                assert sha((folder / f"{split}.jsonl").read_bytes()) == seal[f"{split}_sha256"]
                cases.extend(
                    {**c, "original_split": split} for c in rows(folder / f"{split}.jsonl")
                )
            backlog = load(folder / "regression_backlog.json")
            known = {c["case_id"]: c for c in backlog["cases"]}
            assert len(known) == 12
            report["evaluation_role"] = "opened 50-case regression; original splits are provenance"
            for case in cases:
                started = time.perf_counter()
                actual = await retriever.retrieve(case["question"])
                metrics = score(case, actual, retriever.chunks)["hybrid_rerank"]
                row = {
                    "case_id": case["case_id"],
                    "original_split": case["original_split"],
                    "question": case["question"],
                    "scores": metrics,
                    "retrieval": actual,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "execution_status": "tested",
                }
                if case["case_id"] in known:
                    baseline = known[case["case_id"]]["baseline"]
                    row["backlog_status"] = "deferred_known_failure"
                    row["baseline"] = baseline
                    row["baseline_delta"] = {
                        "groups_covered_at5": metrics["groups_covered"]
                        - baseline["groups_covered_at5"],
                        "budget_complete_coverage": int(metrics["budget_complete_coverage"])
                        - int(baseline["budget_complete_coverage"]),
                    }
                report["rows"].append(row)
                for k, v in actual["actual_model_calls"].items():
                    report["model_calls"][k] += v
                write_json(args.output / "results.json", report)
                print(
                    {
                        "regression": len(report["rows"]),
                        "case": case["case_id"],
                        "complete": metrics["complete_coverage_at5"],
                    },
                    flush=True,
                )
            found = {r["case_id"] for r in report["rows"] if "backlog_status" in r}
            assert found == set(known)
            report["backlog_tested_n"] = len(found)
        else:
            cases = business_cases(args.mode, corpus)
            for case in cases:
                history = []
                for number, turn in enumerate(case["turns"], 1):
                    started = time.perf_counter()
                    query = "\n".join(history + [turn["question"]])
                    history.append(turn["question"])
                    gate = combined_boundary(query)
                    skip = None
                    if turn["fault"] != "none":
                        skip = "fault_condition_agent_track_only"
                    elif turn["expected_domain"] != "health":
                        skip = "out_of_domain_scope"
                    elif gate != "retrieve":
                        skip = gate
                    row = {
                        "case_id": case["case_id"],
                        "category": case["category"],
                        "turn": number,
                        "question": turn["question"],
                        "query": query,
                        "boundary": gate,
                        "fault": turn["fault"],
                        "execution_status": "tested",
                    }
                    if skip:
                        row.update(
                            execution_status="not_retrieved",
                            reason=skip,
                            scores=None,
                            model_calls={"embedding": 0, "rerank": 0},
                        )
                    else:
                        actual = await retriever.retrieve(query)
                        row.update(
                            retrieval=actual,
                            scores=score(turn, actual, retriever.chunks)["hybrid_rerank"],
                            model_calls=actual["actual_model_calls"],
                        )
                        for k, v in actual["actual_model_calls"].items():
                            report["model_calls"][k] += v
                    row["elapsed_ms"] = (time.perf_counter() - started) * 1000
                    report["rows"].append(row)
                    write_json(args.output / "results.json", report)
                    print(
                        {
                            "case": case["case_id"],
                            "turn": number,
                            "status": row["execution_status"],
                        },
                        flush=True,
                    )
        report["status"] = "DONE_execution_semantic_review_pending"
    except Exception as error:
        report["status"] = "execution_error"
        report["error_type"] = type(error).__name__
        raise
    finally:
        await retriever.close()
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(args.output / "results.json", report)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["dev", "heldout", "regressions"])
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--approval", type=Path)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
