"""Validate source mappings, fixed-budget packs and v3 metrics before publishing aggregates."""

import json
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_schema import validate_gold
from rag_portfolio.evaluation.medical_v3 import (
    DOMAIN,
    EXPERIMENT,
    VARIANTS,
    ContextPacker,
    GoldV3,
    SuiteV3,
    aggregate,
    budget_metrics,
    coverage_order,
    group_hits,
    input_action,
    metrics,
    plan_query,
    validate_selection,
)
from rag_portfolio.evaluation.retrievers import Candidate
from rag_portfolio.literature_backup import LiteratureError, file_hash, write_json


def validate_report(report, suite, gold, manifest, pages, chunks, aliases):
    required = {
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "status": "complete",
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "answer_generation_evaluated": False,
        "clinical_approval": False,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "document_count": len(manifest["documents"]),
    }
    if any(report.get(k) != v for k, v in required.items()):
        raise LiteratureError("Incomplete, foreign or changed medical v3 report")
    if report["retrieval_config_fingerprint"] != digest(report["config"]):
        raise LiteratureError("V3 configuration fingerprint differs")
    validate_gold(gold, suite, manifest, pages, chunks)
    lookup = {c["chunk_id"]: c for c in chunks}
    docs = {c["document_id"]: {"title": c["title"], "metadata": c["metadata"]} for c in chunks}
    packer = ContextPacker(pages, lookup)
    gold_cases = {c.id: c for c in gold.cases}
    case_map = {c.id: c for c in suite.cases if c.split == report["split"]}
    if len(report["cases"]) != len(case_map) or {r["id"] for r in report["cases"]} != set(case_map):
        raise LiteratureError("V3 report cases differ from sealed suite")
    for row in report["cases"]:
        case, gcase = case_map[row["id"]], gold_cases[row["id"]]
        expected = {
            "query": case.query,
            "category": case.category,
            "split": case.split,
            "scored": gcase.scored,
            "answerable": gcase.answerable,
            "expected_action": case.expected_action,
            "patient_value_generated": False,
        }
        if any(row.get(k) != v for k, v in expected.items()):
            raise LiteratureError("V3 report question or input declaration differs")
        action = input_action(case.query, aliases)
        if row["input_action"] != action or row["action_correct"] != (
            action == case.expected_action
        ):
            raise LiteratureError("V3 input routing record differs")
        sources, tasks = plan_query(case.query, aliases, docs)
        if row["resolved_sources"] != sources or row["tasks"] != [asdict(t) for t in tasks]:
            raise LiteratureError("V3 query plan differs from query-only rules")
        if set(row["variants"]) != set(VARIANTS):
            raise LiteratureError("V3 report variants incomplete")
        candidates_by_variant = {}
        for name, output in row["variants"].items():
            ids = output["candidate_ids"]
            if len(set(ids)) != len(ids) or any(i not in lookup for i in ids):
                raise LiteratureError("V3 candidate identity differs")
            if [c["chunk_id"] for c in output["candidates"]] != ids:
                raise LiteratureError("V3 candidate records differ")
            if [c["chunk_id"] for c in output["top5"]] != ids[:5]:
                raise LiteratureError("V3 Top-5 differs from candidate ranking")
            for card in output["top5"]:
                source = lookup[card["chunk_id"]]
                if any(
                    card[k] != source[k]
                    for k in (
                        "document_id",
                        "title",
                        "text",
                        "start_char",
                        "end_char",
                        "pdf_page_start",
                        "pdf_page_end",
                        "source_path",
                        "source_sha256",
                    )
                ):
                    raise LiteratureError("V3 evidence source mapping differs")
                if text_digest(card["text"]) != source["text_sha256"]:
                    raise LiteratureError("V3 source text hash differs")
            candidates = [
                Candidate(
                    UUID(c["chunk_id"]),
                    c["score"],
                    dense_rank=c.get("dense_rank"),
                    sparse_rank=c.get("sparse_rank"),
                )
                for c in output["candidates"]
            ]
            candidates_by_variant[name] = candidates
            expected_pack = packer.pack(candidates, expand=name == "context_expanded")
            if output["context_pack"] != expected_pack:
                raise LiteratureError("V3 context pack, source intervals or token budget differs")
            if output["metrics"] != metrics(output["top5"], gcase) or output[
                "budget_metrics"
            ] != budget_metrics(expected_pack["cards"], gcase):
                raise LiteratureError("V3 metrics differ from frozen source facts")
            all_cards = [lookup[key] for key in ids]
            for field, cards in (
                ("required_group_hits", output["top5"]),
                ("candidate_group_hits", all_cards),
                ("budget_group_hits", expected_pack["cards"]),
            ):
                if output[field] != group_hits(cards, gcase):
                    raise LiteratureError("V3 evidence coverage differs")
        task_rankings = {
            k: [Candidate(UUID(c["chunk_id"]), c["score"]) for c in hits]
            for k, hits in row["task_rankings"].items()
        }
        ordered, audit = coverage_order(candidates_by_variant["decomposed"], task_rankings)
        if [str(c.chunk_id) for c in ordered] != row["variants"]["coverage"][
            "candidate_ids"
        ] or audit != row["coverage_selection_audit"]:
            raise LiteratureError("V3 coverage selector differs from actual subquery ranks")
    if report["summary"] != aggregate(report["cases"]):
        raise LiteratureError("V3 summary differs")
    expected_input = {
        "count": len(report["cases"]),
        "correct": sum(r["action_correct"] for r in report["cases"]),
        "patient_values_generated": 0,
        "evaluation_scope": "deterministic input routing only; no generated-answer evaluation",
    }
    if report["input_action_summary"] != expected_input:
        raise LiteratureError("V3 input routing summary differs")


def calibration(root, dev):
    rows = {r["query"]: r for r in dev["cases"]}
    result = {}
    for split in ("dev", "test"):
        old = json.loads(
            (root / f".local/medical_rehab_v2/results/{split}/latest.json").read_text(
                encoding="utf8"
            )
        )
        result[split] = {}
        for name, original in (
            ("hybrid", "hybrid"),
            ("reranked_hybrid", "reranked_hybrid"),
            ("v2_source_routed_rerank", "source_routed_rerank"),
        ):
            paired = [(r, rows[r["query"]]) for r in old["cases"] if r["scored"]]
            before = sum(
                a["variants"][original]["metrics"]["complete_evidence_at_5"] for a, b in paired
            )
            after = sum(b["variants"][name]["metrics"]["complete_evidence_at_5"] for a, b in paired)
            if any(
                a["variants"][original]["candidate_ids"] != b["variants"][name]["candidate_ids"]
                for a, b in paired
            ):
                raise LiteratureError("Calibration must use identical archived rankings")
            result[split][name] = {
                "case_count": len(paired),
                "old_gold_complete_count": before,
                "v3_gold_complete_count": after,
                "ranking_unchanged": True,
                "interpretation": "annotation/metric change only, not retrieval improvement",
            }
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    base, public = root / ".local" / EXPERIMENT, root / "eval" / EXPERIMENT
    suite = SuiteV3.model_validate_json((public / "cases.json").read_text(encoding="utf8"))
    gold = GoldV3.model_validate_json((public / "gold.json").read_text(encoding="utf8"))
    aliases = json.loads((public / "source_aliases.json").read_text(encoding="utf8"))
    manifest, pages, chunks = load_corpus(
        root / ".local/medical_rehab_v1/corpus/manifest.json", root / "doc"
    )
    dev_path, test_path = base / "results/dev/latest.json", base / "results/test/latest.json"
    dev = json.loads(dev_path.read_text(encoding="utf8"))
    test = json.loads(test_path.read_text(encoding="utf8"))
    for report in (dev, test):
        validate_report(report, suite, gold, manifest, pages, chunks, aliases)
    selection = json.loads((base / "selection.json").read_text(encoding="utf8"))
    validate_selection(selection, dev, gold, test["embedding_revision"], test["config"])
    payload = {
        k: dev[k]
        for k in (
            "domain",
            "experiment_id",
            "corpus_fingerprint",
            "suite_fingerprint",
            "test_seal",
            "embedding_model",
            "embedding_dimension",
            "rerank_model",
            "config",
            "document_count",
            "page_count",
            "chunk_count",
            "clinical_approval",
            "answer_generation_evaluated",
            "limitations",
        )
    }
    payload["selected_on_development"] = selection["variant"]
    payload["baseline_calibration"] = calibration(root, dev)
    payload["splits"] = {}
    for report, path in ((dev, dev_path), (test, test_path)):
        payload["splits"][report["split"]] = {
            "report_sha256": file_hash(path),
            "created_at": report["created_at"],
            "summary": report["summary"],
            "model_calls": report["model_calls"],
            "elapsed_seconds": report["elapsed_seconds"],
            "case_count": len(report["cases"]),
            "input_action_summary": report["input_action_summary"],
            "diagnostics": [
                {
                    "id": r["id"],
                    "category": r["category"],
                    "scored": r["scored"],
                    "input_action": r["input_action"],
                    "action_correct": r["action_correct"],
                    "baseline_replayed": r["baseline_replayed"],
                    "variants": {
                        n: {
                            "candidate_group_hits": v["candidate_group_hits"],
                            "required_group_hits": v["required_group_hits"],
                            "budget_group_hits": v["budget_group_hits"],
                            "reference_tokens_used": v["context_pack"]["reference_tokens_used"],
                        }
                        for n, v in r["variants"].items()
                    },
                }
                for r in report["cases"]
            ],
        }
    output = root / "artifacts/eval/medical_rehab_v3/summary.json"
    write_json(output, payload)
    print(f"Validated and published medical v3 aggregate summary: {output}")


if __name__ == "__main__":
    main()
