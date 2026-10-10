"""Validate private v2 evidence and publish aggregates without patient data or PDF bodies."""

import json
from pathlib import Path

from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_schema import case_metrics, validate_gold
from rag_portfolio.evaluation.medical_v2 import (
    DOMAIN,
    EXPERIMENT,
    VARIANTS,
    GoldV2,
    SuiteV2,
    aggregate,
    validate_selection,
)
from rag_portfolio.literature_backup import LiteratureError, file_hash, write_json


def validate_report(report, suite, gold, manifest, pages, chunks):
    required = {
        "status": "complete",
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "answer_generation_evaluated": False,
        "clinical_approval": False,
    }
    if any(report.get(k) != v for k, v in required.items()):
        raise LiteratureError("Incomplete, changed or foreign medical v2 report")
    if report["retrieval_config_fingerprint"] != digest(report["config"]):
        raise LiteratureError("Medical v2 config fingerprint differs")
    bindings = validate_gold(gold, suite, manifest, pages, chunks)
    lookup = {c["chunk_id"]: c for c in chunks}
    expected = {c.id for c in suite.cases if c.split == report["split"]}
    if {r["id"] for r in report["cases"]} != expected or len(report["cases"]) != len(expected):
        raise LiteratureError("Report case IDs differ from the sealed suite")
    cases = {c.id: c for c in suite.cases}
    gold_cases = {c.id: c for c in gold.cases}
    for row in report["cases"]:
        case, gcase = cases[row["id"]], gold_cases[row["id"]]
        if row["query"] != case.query or row["scored"] != gcase.scored:
            raise LiteratureError("Report question/scoring changed")
        if set(row["variants"]) != set(VARIANTS):
            raise LiteratureError("Report variants incomplete")
        for output in row["variants"].values():
            ids = output["candidate_ids"]
            if len(set(ids)) != len(ids) or any(i not in lookup for i in ids):
                raise LiteratureError("Report candidates are duplicated or foreign")
            if [c["chunk_id"] for c in output["top5"]] != ids[:5]:
                raise LiteratureError("Report Top-5 differs from candidate order")
            for card in output["top5"]:
                source = lookup[card["chunk_id"]]
                if any(
                    card[k] != source[k]
                    for k in (
                        "document_id",
                        "source_path",
                        "source_sha256",
                        "title",
                        "text",
                        "start_char",
                        "end_char",
                        "pdf_page_start",
                        "pdf_page_end",
                    )
                ):
                    raise LiteratureError("Report evidence/source mapping differs")
                if text_digest(card["text"]) != source["text_sha256"]:
                    raise LiteratureError("Report evidence text hash differs")
            expected_metrics = case_metrics(ids[:5], bindings[case.id]) if gcase.scored else None
            if output["metrics"] != expected_metrics:
                raise LiteratureError("Report metrics differ from frozen Gold")
            for field, ranking in (("required_group_hits", ids[:5]), ("candidate_group_hits", ids)):
                if output[field] != [bool(set(ranking) & g) for g in bindings[case.id]]:
                    raise LiteratureError("Report coverage differs from frozen Gold")
    if report["summary"] != aggregate(report["cases"]):
        raise LiteratureError("Report aggregate differs")


def main():
    root = Path(__file__).resolve().parents[1]
    base = root / ".local" / EXPERIMENT
    public = root / "eval" / EXPERIMENT
    suite = SuiteV2.model_validate_json((public / "cases.json").read_text(encoding="utf8"))
    gold = GoldV2.model_validate_json((public / "gold.json").read_text(encoding="utf8"))
    manifest, pages, chunks = load_corpus(
        root / ".local/medical_rehab_v1/corpus/manifest.json", root / "doc"
    )
    dev_path = base / "results/dev/latest.json"
    test_path = base / "results/test/latest.json"
    dev = json.loads(dev_path.read_text(encoding="utf8"))
    test = json.loads(test_path.read_text(encoding="utf8"))
    for report in (dev, test):
        validate_report(report, suite, gold, manifest, pages, chunks)
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
    payload["splits"] = {}
    for report, path in ((dev, dev_path), (test, test_path)):
        payload["splits"][report["split"]] = {
            "report_sha256": file_hash(path),
            "created_at": report["created_at"],
            "summary": report["summary"],
            "model_calls": report["model_calls"],
            "elapsed_seconds": report["elapsed_seconds"],
            "case_count": len(report["cases"]),
            "unscored_count": sum(not r["scored"] for r in report["cases"]),
            "diagnostics": [
                {
                    "id": row["id"],
                    "category": row["category"],
                    "scored": row["scored"],
                    "resolved_sources": row["resolved_sources"],
                    "variants": {
                        n: {
                            "candidate_group_hits": v["candidate_group_hits"],
                            "required_group_hits": v["required_group_hits"],
                        }
                        for n, v in row["variants"].items()
                    },
                }
                for row in report["cases"]
            ],
        }
    output = root / "artifacts/eval/medical_rehab_v2/summary.json"
    write_json(output, payload)
    print(f"Validated and published aggregate summary: {output}")


if __name__ == "__main__":
    main()
