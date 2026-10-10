"""Publish aggregate medical metrics and diagnostics without private PDF evidence text."""

import json
from pathlib import Path

from rag_portfolio.evaluation.experiments import MEDICAL_DOMAIN, MEDICAL_EXPERIMENT
from rag_portfolio.evaluation.medical_corpus import digest, load_corpus
from rag_portfolio.evaluation.medical_runner import RETRIEVAL_CONFIG, VARIANTS
from rag_portfolio.evaluation.medical_schema import (
    MedicalGold,
    case_metrics,
    read_suite,
    validate_gold,
)
from rag_portfolio.literature_backup import LiteratureError, file_hash, write_json


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    base = root / ".local/medical_rehab_v1"
    manifest, pages, chunks = load_corpus(base / "corpus/manifest.json", root / "doc")
    suite = read_suite(root / "eval/medical_rehab_v1/cases.json")
    gold = MedicalGold.model_validate_json(
        (root / "eval/medical_rehab_v1/gold.json").read_text(encoding="utf-8")
    )
    bindings = validate_gold(gold, suite, manifest, pages, chunks)
    lookup = {chunk["chunk_id"]: chunk for chunk in chunks}
    cases = {case.id: case for case in suite.cases}
    gold_cases = {case.id: case for case in gold.cases}
    summary = {
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "status": "complete_offline_experiment",
        "clinical_approval": False,
        "answer_generation_evaluated": False,
        "document_count": len(manifest["documents"]),
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "flagged_pages": manifest["flagged_pages"],
        "corpus_fingerprint": manifest["corpus_fingerprint"],
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "retrieval_config": RETRIEVAL_CONFIG,
        "retrieval_config_fingerprint": digest(RETRIEVAL_CONFIG),
        "splits": {},
    }
    revision = None
    for split in ("dev", "test"):
        path = base / f"results/{split}/latest.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "status": "complete",
            "domain": MEDICAL_DOMAIN,
            "experiment_id": MEDICAL_EXPERIMENT,
            "split": split,
            "corpus_fingerprint": gold.corpus_fingerprint,
            "suite_fingerprint": gold.suite_fingerprint,
            "test_seal": gold.test_seal,
            "config": RETRIEVAL_CONFIG,
        }
        if any(report.get(key) != value for key, value in required.items()):
            raise LiteratureError("Only matching complete medical reports may be summarized")
        if revision is not None and revision != report["embedding_revision"]:
            raise LiteratureError("Development/test embedding revisions differ")
        revision = report["embedding_revision"]
        summary["embedding"] = {
            "revision": revision,
            "provider": report["embedding_provider"],
            "model": report["embedding_model"],
            "dimension": report["embedding_dimension"],
            "collection": report["collection"],
            "backend": report["backend"],
        }
        rows = report["cases"]
        if len({row["id"] for row in rows}) != len(rows) or {row["id"] for row in rows} != {
            case.id for case in suite.cases if case.split == split
        }:
            raise LiteratureError("Report cases do not match the sealed suite")
        diagnostics = []
        for row in rows:
            case = cases[row["id"]]
            gold_case = gold_cases[row["id"]]
            if (
                row["query"] != case.query
                or row["filters"] != case.filters.model_dump(exclude_none=True)
                or row["scored"] != gold_case.scored
                or set(row["variants"]) != set(VARIANTS)
            ):
                raise LiteratureError("Report case differs from the frozen question")
            variants = {}
            for name, result in row["variants"].items():
                for card in result["top5"]:
                    chunk = lookup[card["chunk_id"]]
                    for key in (
                        "document_id",
                        "source_sha256",
                        "source_path",
                        "text",
                        "pdf_page_start",
                        "pdf_page_end",
                        "start_char",
                        "end_char",
                    ):
                        if card[key] != chunk[key]:
                            raise LiteratureError("Reported evidence differs from its source chunk")
                ids = [card["chunk_id"] for card in result["top5"]]
                expected = case_metrics(ids, bindings[case.id]) if gold_case.scored else None
                if result["metrics"] != expected:
                    raise LiteratureError("Reported scores differ from frozen source-span Gold")
                candidates = result["candidate_ids"]
                if any(key not in lookup for key in candidates) or ids != candidates[:5]:
                    raise LiteratureError("Candidate list and evidence cards differ")
                groups = bindings[case.id]
                variants[name] = {
                    "required_groups": len(groups),
                    "group_hits_top5": sum(bool(set(ids) & group) for group in groups),
                    "group_hits_candidate_union": sum(
                        bool(set(candidates) & group) for group in groups
                    ),
                    "top5_declared_filter_mismatches": sum(
                        not case.filters.matches(lookup[key]["metadata"]) for key in ids
                    ),
                    "top5_source_ids": [lookup[key]["document_id"] for key in ids],
                }
            diagnostics.append({"id": case.id, "scored": gold_case.scored, "variants": variants})
        for name in VARIANTS:
            metrics = [row["variants"][name]["metrics"] for row in rows if row["scored"]]
            aggregate = report["summary"][name]
            if aggregate["scored_count"] != len(metrics) or any(
                aggregate[key] != sum(item[key] for item in metrics) / len(metrics)
                for key in metrics[0]
            ):
                raise LiteratureError("Aggregate scores differ from individual cases")
        score_ranges = {}
        for label, selected in (
            ("scored", [row for row in rows if row["scored"]]),
            ("missing_input", [row for row in rows if not row["answerable"]]),
        ):
            values = [row["variants"]["dense"]["top5"][0]["score"] for row in selected]
            score_ranges[label] = {"count": len(values), "min": min(values), "max": max(values)}
        summary["splits"][split] = {
            "created_at": report["created_at"],
            "report_sha256": file_hash(path),
            "query_count": len(rows),
            "scored_count": sum(row["scored"] for row in rows),
            "summary": report["summary"],
            "model_calls": report["model_calls"],
            "elapsed_seconds": report["elapsed_seconds"],
            "dense_top1_score_ranges": score_ranges,
            "diagnostics": diagnostics,
        }
    history = []
    for path in sorted((base / "results").glob("*/runs/*/report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("status") == "complete" and report.get("domain") == MEDICAL_DOMAIN:
            history.append(
                {
                    "split": report["split"],
                    "created_at": report["created_at"],
                    "model_calls": report["model_calls"],
                    "elapsed_seconds": report["elapsed_seconds"],
                    "report_sha256": file_hash(path),
                }
            )
    summary["run_history"] = history
    output = root / "artifacts/eval/medical_rehab_v1/summary.json"
    write_json(output, summary)
    print(f"Verified aggregate report: {output}")


if __name__ == "__main__":
    main()
