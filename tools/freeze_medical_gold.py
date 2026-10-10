"""Bind explicitly reviewed original-text anchors, then seal the separate medical test set."""

import argparse
import json
import re
from pathlib import Path

from rag_portfolio.evaluation.experiments import medical_path
from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_schema import (
    MedicalGold,
    read_suite,
    test_seal,
    validate_gold,
)
from rag_portfolio.literature_backup import LiteratureError, write_json


def anchor_span(page: dict, anchor: str) -> dict:
    positions = [i for i, char in enumerate(page["text"]) if not char.isspace()]
    compact = "".join(page["text"][i] for i in positions)
    needle = re.sub(r"\s+", "", anchor)
    matches = list(re.finditer(re.escape(needle), compact))
    if len(matches) != 1:
        raise LiteratureError(
            "Reviewed anchor missing or ambiguous: "
            f"{page['document_id']}/{page['pdf_page']}/{anchor}"
        )
    start, end = positions[matches[0].start()], positions[matches[0].end() - 1] + 1
    return {
        "document_id": page["document_id"],
        "pdf_page": page["pdf_page"],
        "start_char": start,
        "end_char": end,
        "text_sha256": text_digest(page["text"][start:end]),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reviews", type=Path, default=root / "eval/medical_rehab_v1/source_reviews.json"
    )
    parser.add_argument(
        "--corpus", type=Path, default=root / ".local/medical_rehab_v1/corpus/manifest.json"
    )
    args = parser.parse_args()
    reviews = json.loads(medical_path(root, args.reviews, public=True).read_text(encoding="utf-8"))
    suite_path = root / "eval/medical_rehab_v1/cases.json"
    suite = read_suite(suite_path)
    if (
        reviews.get("domain") != "medical_knowledge"
        or reviews.get("review_scope") != "source_text_and_provenance"
    ):
        raise LiteratureError("Explicit medical source-text review is required")
    if len(reviews["cases"]) != len(suite.cases) or {row["id"] for row in reviews["cases"]} != {
        case.id for case in suite.cases
    }:
        raise LiteratureError("Every candidate requires its own review record")
    manifest, pages, chunks = load_corpus(medical_path(root, args.corpus), root / "doc")
    page_map = {(page["document_id"], page["pdf_page"]): page for page in pages}
    gold_cases = []
    for review in reviews["cases"]:
        groups = []
        for group in review["required_groups"]:
            spans = [
                anchor_span(page_map[(ref["document_id"], ref["pdf_page"])], ref["anchor"])
                for ref in group["alternatives"]
            ]
            groups.append({"id": group["id"], "support": group["support"], "alternatives": spans})
        gold_cases.append(
            {
                key: review[key]
                for key in (
                    "id",
                    "answerable",
                    "scored",
                    "expected_behavior",
                    "review_reason",
                    "reviewer",
                    "review_scope",
                )
            }
            | {"required_groups": groups}
        )
    reviewed_suite = suite.model_copy(
        update={
            "cases": tuple(
                case.model_copy(update={"review_status": "text_verified"}) for case in suite.cases
            )
        }
    )
    payload = {
        "schema_version": 1,
        "domain": "medical_knowledge",
        "experiment_id": "medical_rehab_v1",
        "corpus_fingerprint": manifest["corpus_fingerprint"],
        "suite_fingerprint": digest(reviewed_suite.model_dump(mode="json")),
        "status": "frozen_technical_review",
        "clinical_approval": False,
        "cases": gold_cases,
        "test_seal": test_seal(reviewed_suite, gold_cases, manifest["corpus_fingerprint"]),
    }
    gold = MedicalGold.model_validate(payload)
    bindings = validate_gold(gold, reviewed_suite, manifest, pages, chunks)
    destination = root / "eval/medical_rehab_v1/gold.json"
    if destination.exists() and json.loads(
        destination.read_text(encoding="utf-8")
    ) != gold.model_dump(mode="json"):
        raise LiteratureError("Gold is already sealed; changes require a new experiment revision")
    write_json(suite_path, reviewed_suite.model_dump(mode="json"))
    write_json(destination, gold.model_dump(mode="json"))
    dev_count = sum(c.split == "dev" for c in suite.cases)
    test_count = sum(c.split == "test" for c in suite.cases)
    print(
        f"Frozen technical Gold: {len(bindings)} cases; dev={dev_count}; test={test_count}; "
        "clinical_approval=False"
    )


if __name__ == "__main__":
    main()
