"""Build the live index; evaluate sealed dev, select once, then sealed heldout once."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from rag_portfolio.public_medical import canonical, immutable_json, sha, write_json
from rag_portfolio.public_medical_retrieval import (
    STRATEGIES,
    PublicMedicalRetriever,
    build_index,
    input_text,
    validate_index,
)
from rag_portfolio.services.retrieval_algorithms import LocalBM25Retriever

GOLD = Path("eval/public_medical_corpus_v1")
PUBLIC = Path("artifacts/public-medical-corpus-v1/public-prose.json")
LOCAL = Path(".local/public_medical_corpus_v1")


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def sealed_gold(split, corpus):
    seal = load(GOLD / "seal.json")
    data = (GOLD / f"{split}.jsonl").read_bytes()
    if sha(data) != seal[split + "_sha256"] or corpus["corpus_sha256"] != seal["corpus_sha256"]:
        raise ValueError("Gold/corpus seal drift")
    return [json.loads(line) for line in data.splitlines()]


def evidence_covered(group, spans):
    # Union of same-paragraph offsets supports expanded adjacent chunks without double counting.
    for gold in group["acceptable"]:
        ranges = sorted(
            (s["start"], s["end"]) for s in spans if s["segment_id"] == gold["segment_id"]
        )
        cursor = gold["start"]
        for start, end in ranges:
            if start <= cursor:
                cursor = max(cursor, end)
        if cursor >= gold["end"]:
            return True
    return False


def score(case, retrieval, chunks):
    groups = case["evidence_groups"]
    rows = {}
    for strategy, result in retrieval["strategies"].items():
        top = [chunks[r["chunk_id"]] for r in result["top5"]]
        hits = [any(evidence_covered(g, [chunk]) for g in groups) for chunk in top]
        rows[strategy] = {
            "answerable": bool(groups),
            "hit_at1": bool(hits and hits[0]),
            "hit_at5": any(hits),
            "mrr_at5": next((1 / (i + 1) for i, hit in enumerate(hits) if hit), 0),
            "complete_coverage_at5": bool(groups) and all(evidence_covered(g, top) for g in groups),
            "budget_complete_coverage": bool(groups)
            and all(evidence_covered(g, result["context"]) for g in groups),
            "groups_covered": sum(evidence_covered(g, top) for g in groups),
            "groups_total": len(groups),
            "negative_refused_by_input_gate": not groups and retrieval["boundary"] != "retrieve",
            "false_refusal_by_input_gate": bool(groups) and retrieval["boundary"] != "retrieve",
            "raw_retrieval_returned_hits_on_negative": not groups and bool(top),
            "context_tokens": result["context_tokens"],
        }
    return rows


def aggregate(rows):
    out = {}
    for strategy in STRATEGIES:
        values = [r["scores"][strategy] for r in rows]
        answerable = [v for v in values if v["answerable"]]
        negatives = [v for v in values if not v["answerable"]]
        out[strategy] = {
            "answerable_n": len(answerable),
            "negative_n": len(negatives),
            **{
                key: sum(v[key] for v in answerable) / len(answerable)
                for key in [
                    "hit_at1",
                    "hit_at5",
                    "mrr_at5",
                    "complete_coverage_at5",
                    "budget_complete_coverage",
                ]
            },
            "input_gate_negative_refused": sum(
                v["negative_refused_by_input_gate"] for v in negatives
            ),
            "input_gate_false_refusal": sum(v["false_refusal_by_input_gate"] for v in answerable),
            "raw_retrieval_negative_with_hits": sum(
                v["raw_retrieval_returned_hits_on_negative"] for v in negatives
            ),
            "max_context_tokens": max(v["context_tokens"] for v in values),
        }
    return out


async def main(args):
    corpus = load(PUBLIC)
    index_path = args.index_path
    if args.command == "index":
        index = await build_index(corpus, index_path)
        validate_index(corpus, index)
        immutable_json(index_path, index)
        immutable_json(
            args.index_manifest,
            {k: v for k, v in index.items() if k != "rows"}
            | {
                "chunk_count": len(index["rows"]),
                "storage": "ignored local normalized vectors; exact cosine scan, "
                "no Qdrant dependency",
            },
        )
        print({"index_sha256": index["index_sha256"], "count": len(index["rows"])})
        return
    if args.output.exists():
        raise ValueError("All attempts are retained; choose a new output directory")
    args.output.mkdir(parents=True)
    index = load(index_path)
    retriever = PublicMedicalRetriever(corpus, index)
    chunks = {c["chunk_id"]: c for c in corpus["chunks"]}
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "corpus_sha256": corpus["corpus_sha256"],
        "index_sha256": index["index_sha256"],
        "gold_seal": load(GOLD / "seal.json"),
        "status": "running",
        "splits": {},
        "errors": [],
    }
    try:
        for split in ["dev", "heldout"]:
            rows = []
            for case in sealed_gold(split, corpus):
                actual = await retriever.retrieve(case["question"])
                row = {
                    "case_id": case["case_id"],
                    "question": case["question"],
                    "expected_behavior": case["expected_behavior"],
                    "retrieval": actual,
                    "scores": score(case, actual, chunks),
                }
                rows.append(row)
                with (args.output / f"{split}.jsonl").open("ab") as stream:
                    stream.write(canonical(row) + b"\n")
                print(f"{split} {len(rows)} completed", flush=True)
            metrics = aggregate(rows)
            report["splits"][split] = metrics
            if split == "dev":
                chosen = max(
                    STRATEGIES,
                    key=lambda s: (
                        metrics[s]["budget_complete_coverage"],
                        metrics[s]["complete_coverage_at5"],
                        metrics[s]["mrr_at5"],
                        -STRATEGIES.index(s),
                    ),
                )
                immutable_json(
                    args.output / "dev-selection.json",
                    {
                        "selected": chosen,
                        "selected_at": datetime.now(UTC).isoformat(),
                        "metrics": metrics,
                        "heldout_read_before_selection": False,
                    },
                )
                report["selected_strategy"] = chosen
            write_json(args.output / "summary.json", report)
        # Combined local lexical check, without external rights-held prose or unchanged old Gold.
        combined = load(LOCAL / "corpora/medical_combined_v1-prose-2.json")
        bm25 = LocalBM25Retriever({UUID(c["chunk_id"]): input_text(c) for c in combined["chunks"]})
        combined_checks = []
        for query in [
            "D040 中国脑卒中康复治疗指南 2011 2012",
            "D042 中国膝骨关节炎康复治疗指南 2023 2024",
            "D041 脑卒中 智能 康复 共识",
        ]:
            matches = await bm25.search(query, limit=5)
            combined_checks.append({"query": query, "top5": [str(c.chunk_id) for c in matches]})
        write_json(
            args.output / "combined-bm25.json",
            {
                "corpus_sha256": combined["corpus_sha256"],
                "checks": combined_checks,
                "full_hybrid": "BLOCKED: old resource rights held; "
                "no old prose sent to external models",
            },
        )
        report["status"] = "DONE_technical_eval_human_review_pending"
        report["finished_at"] = datetime.now(UTC).isoformat()
    except Exception as error:
        report["status"] = "BLOCKED"
        report["errors"].append(
            {
                "type": type(error).__name__,
                "message": "Provider/index/evaluation unavailable; inspect sanitized local logs",
            }
        )
        raise
    finally:
        await retriever.close()
        write_json(args.output / "summary.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["index", "eval"])
    parser.add_argument(
        "--index-path", type=Path, default=LOCAL / "indexes/medical-public-v1-dense-1.json"
    )
    parser.add_argument(
        "--index-manifest",
        type=Path,
        default=Path("artifacts/public-medical-corpus-v1/index_manifest.json"),
    )
    parser.add_argument("--output", type=Path, default=LOCAL / "runs/run-1")
    asyncio.run(main(parser.parse_args()))
