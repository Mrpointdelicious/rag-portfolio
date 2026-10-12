"""Recompute preservation, work dedup and exact PDF/JATS anchor mappings locally."""

import json
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from rag_portfolio.public_medical import article_xml, sha, write_json

LOCAL = Path(".local/public_medical_corpus_v1")
OUT = Path("artifacts/public-medical-corpus-v1")


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).replace("\u00ad", ""))


def main():
    baseline = load(LOCAL / "legacy-baseline.json")
    changes = [
        p
        for p, digest in baseline["files"].items()
        if not Path(p).exists() or sha(Path(p).read_bytes()) != digest
    ]
    preservation = {
        "baseline_sha256": sha((LOCAL / "legacy-baseline.json").read_bytes()),
        "file_count": len(baseline["files"]),
        "changed": changes,
        "unchanged": not changes,
        "old_corpus": "8 PDFs / 102 pages / 736 chunks; "
        "v1/v2/v3 source/Gold/ranking/reports immutable",
    }
    write_json(OUT / "preservation.json", preservation)
    sources = load(LOCAL / "original-source-review.json")
    d041 = Path(next(s["path"] for s in sources if s["id"] == "D041"))
    d102 = next(Path("doc/unselected").glob("D102__*.pdf"))
    identities = []
    for code, path in [("D041", d041), ("D102", d102)]:
        reader = PdfReader(path)
        text = "".join("".join((p.extract_text() or "").split()) for p in reader.pages)
        identities.append(
            {
                "id": code,
                "sha256": sha(path.read_bytes()),
                "normalized_text_sha256": sha(text.encode()),
                "pages": len(reader.pages),
            }
        )
    public = load(OUT / "public-prose.json")
    dois = {d["doi"].casefold(): d["document_id"] for d in public["documents"]}
    for d in load(OUT / "rights_audit.json")["original_eight"]:
        dois[d["doi"].casefold()] = d["document_id"]
    edges = []
    for d in public["documents"]:
        article = article_xml(LOCAL / "assets" / d["document_id"] / "article.xml", d["doi"])
        for ref in article["references"]:
            for doi in ref["dois"]:
                if doi in dois:
                    edges.append(
                        {
                            "source": d["document_id"],
                            "target": dois[doi],
                            "relation": "cites; not an independent participant cohort",
                            "reference_id": ref["id"],
                            "doi": doi,
                        }
                    )
    write_json(
        OUT / "dedup_report.json",
        {
            "file_vs_work": identities,
            "D041_D102_same_normalized_text": identities[0]["normalized_text_sha256"]
            == identities[1]["normalized_text_sha256"],
            "decision": "D102 excluded as duplicate work; D041 retained unchanged",
            "reference_edges": edges,
            "cohort_note": "Citation links do not establish participant disjointness; "
            "review/RCT nesting must not be summed as independent patients; "
            "no clinical evidence aggregation performed",
            "independent_cohort_count": None,
        },
    )
    pdf_pages = {}
    for d in public["documents"]:
        path = LOCAL / "assets" / d["document_id"] / "article.pdf"
        pdf_pages[d["document_id"]] = [
            normalized(p.extract_text() or "") for p in PdfReader(path).pages
        ]
    maps = []
    for split in ["dev", "heldout"]:
        for row in (
            (Path("eval/public_medical_corpus_v1") / f"{split}.jsonl")
            .read_text(encoding="utf8")
            .splitlines()
        ):
            case = json.loads(row)
            for i, group in enumerate(case["evidence_groups"]):
                anchor = group["preferred"][0]
                needle = normalized(anchor["text"])
                pages = [
                    p + 1
                    for p, text in enumerate(pdf_pages[anchor["document_id"]])
                    if needle in text
                ]
                maps.append(
                    {
                        "case_id": case["case_id"],
                        "group_index": i,
                        "document_id": anchor["document_id"],
                        "xml_locator": anchor["locator"],
                        "xml_page": None,
                        "anchor_text_sha256": anchor["text_sha256"],
                        "pdf_pages": pages or None,
                        "alignment": "exact whitespace/NFKC-normalized full sentence"
                        if pages
                        else "not_aligned; do not infer a page",
                    }
                )
    write_json(
        OUT / "pdf_xml_alignment.json",
        {
            "normalization": "NFKC, strip whitespace and soft hyphens; "
            "full anchor string match only",
            "anchors": maps,
            "matched": sum(bool(m["pdf_pages"]) for m in maps),
            "total": len(maps),
            "xml_page_remains_null": True,
        },
    )
    print(
        {
            "legacy_unchanged": not changes,
            "files": len(baseline["files"]),
            "pdf_aligned": sum(bool(m["pdf_pages"]) for m in maps),
            "anchors": len(maps),
        }
    )
    if changes:
        raise ValueError("Legacy files changed")


if __name__ == "__main__":
    main()
