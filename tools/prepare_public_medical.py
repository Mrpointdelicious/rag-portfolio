"""Audit actual assets and create isolated public-prose and combined-local corpora."""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

from rag_portfolio.public_medical import (
    LICENSE_URL,
    article_xml,
    build_corpus,
    immutable_json,
    pdf_segments,
    sha,
    write_json,
)

OLD = {
    "D001": (
        "10.3724/SP.J.1329.2024.03001",
        "沈滢，殷稚飞，孟殿怀 等",
        "康复学报",
        "2024",
        "2024",
        "license_unknown",
        "https://cjournal.hep.com.cn/2096-0328/CN/1217460202854224019",
    ),
    "D003": (
        "10.16305/j.1007-1334.2024.2401120",
        "朱冉冉，王津翔，潘蓓 等",
        "上海中医药杂志",
        "2024",
        "2024",
        "license_unknown",
        "https://doi.org/10.16305/j.1007-1334.2024.2401120",
    ),
    "D041": (
        "10.3969/j.issn.1001-1242.2025.09.001",
        "潘钰，张皓，谢青，江山，刘沙鑫（执笔）；专家组",
        "中国康复医学杂志",
        "2025",
        "2025",
        "license_unknown",
        "https://doi.org/10.3969/j.issn.1001-1242.2025.09.001",
    ),
    "D040": (
        "10.3969/j.issn.1006-9771.2012.04.001",
        "中华医学会神经病学分会等；张通执笔",
        "中国康复理论与实践",
        "2011",
        "2012",
        "license_unknown",
        "https://www.cjrtponline.com/CN/Y2012/V18/I4/301",
    ),
    "D042": (
        "10.7507/1672-2531.202306145",
        "中华医学会物理医学与康复学分会，四川大学华西医院",
        "中国循证医学杂志",
        "2023",
        "2024",
        "license_unknown",
        "https://www.cjebm.com/article/10.7507/1672-2531.202306145",
    ),
    "D129": (
        "10.1016/j.rehab.2022.101674",
        "Ting-Hsuan Hsu; Chi-Lin Tsai; Ju-Yang Chi; Chih-Yang Hsu; Yen-Nung Lin",
        "Elsevier Masson",
        None,
        "2023",
        "all_rights_reserved",
        "https://www.sciencedirect.com/science/article/pii/S1877065722000471",
    ),
    "D161": (
        "10.3390/brainsci13010092",
        "Alberto Loro et al.",
        "MDPI",
        None,
        "2023",
        "CC-BY-4.0",
        "https://doi.org/10.3390/brainsci13010092",
    ),
    "D173": (
        "10.23736/S1973-9087.21.06887-8",
        "Rocco S. Calabro et al.; CICERONE",
        "Edizioni Minerva Medica",
        None,
        "2021",
        "copyright_reserved_no_redistribution_grant",
        "https://www.minervamedica.it/it/riviste/europa-medicophysica/articolo.php?cod=R33Y2021N03A0460",
    ),
}


def initialize_local_review(root):
    """Fresh authorized local setup; never overwrite a previously frozen snapshot."""
    baseline_path = root / "legacy-baseline.json"
    if not baseline_path.exists():
        files = {}
        folders = [
            Path(f"{prefix}/medical_rehab_v{v}")
            for prefix in ["eval", "artifacts/eval", ".local"]
            for v in [1, 2, 3]
        ]
        folders.append(Path("doc/selected"))
        for folder in folders:
            for file in sorted(folder.rglob("*")):
                if file.is_file():
                    files[file.as_posix()] = sha(file.read_bytes())
        for name in [
            "docs/medical-retrieval-experiment.md",
            "docs/medical-rehab-v3-verification.md",
            "doc/backup_manifest.json",
        ]:
            file = Path(name)
            if file.exists():
                files[name] = sha(file.read_bytes())
        immutable_json(
            baseline_path, {"base_head": "5621efe83129fd6c29586ca5daab4d7b175207f4", "files": files}
        )
    review_path = root / "original-source-review.json"
    if not review_path.exists():
        sources = json.loads(Path("eval/medical_rehab_v1/sources.json").read_text(encoding="utf8"))[
            "documents"
        ]
        review = []
        for source in sources:
            found = list(Path("doc/selected").glob(source["document_id"] + "__*.pdf"))
            if len(found) != 1 or sha(found[0].read_bytes()) != source["sha256"]:
                raise ValueError(
                    "BLOCKED: missing or changed local source " + source["document_id"]
                )
            reader = PdfReader(found[0])
            review.append(
                {
                    "id": source["document_id"],
                    "path": str(found[0]),
                    "sha256": source["sha256"],
                    "pages": len(reader.pages),
                    "metadata": {k: str(v) for k, v in (reader.metadata or {}).items()},
                    "first_page": reader.pages[0].extract_text() or "",
                    "last_pages": "\n".join(p.extract_text() or "" for p in reader.pages[-2:]),
                }
            )
        immutable_json(review_path, review)


def main(args):
    root = args.local
    initialize_local_review(root)
    evidence = json.loads((root / "original-source-review.json").read_text(encoding="utf8"))
    old_sources = json.loads(Path("eval/medical_rehab_v1/sources.json").read_text(encoding="utf8"))[
        "documents"
    ]
    source_map = {s["document_id"]: s for s in old_sources}
    acquisition = json.loads((root / "assets/acquisition.json").read_text(encoding="utf8"))
    candidates = json.loads(
        Path("eval/public_medical_corpus_v1/candidates.json").read_text(encoding="utf8")
    )
    new_map = {s["candidate_id"]: s for s in candidates["additional_six"]}
    originals, public_docs, new_audits, all_docs = [], [], [], []
    for entry in evidence:
        code = entry["id"]
        doi, authors, publisher, edition, year, license_status, url = OLD[code]
        source = source_map[code]
        raw = Path(entry["path"]).read_bytes()
        assert sha(raw) == entry["sha256"]
        segments, parsers = pdf_segments(
            Path(entry["path"]),
            parser_override=source.get("parser_override"),
            text_regions=source.get("text_regions"),
        )
        doc = {
            "document_id": code,
            "work_id": "doi:" + doi.casefold(),
            "doi": doi,
            "title": source["title"],
            "authors": [authors],
            "publisher": publisher,
            "edition": edition,
            "publication_year": year,
            "source_url": url,
            "source_sha256": entry["sha256"],
            "format": "pdf",
            "license": license_status,
            "license_evidence": {
                "local_pdf_sha256": entry["sha256"],
                "pdf_page": 1,
                "publisher_url": url,
            },
            "rights_decision": "hold",
            "approved_scope": "technical_evaluation_nonclinical",
            "clinical_approval": False,
            "segments": segments,
            "parser_versions": parsers,
        }
        all_docs.append(doc)
        originals.append(
            {k: v for k, v in doc.items() if k not in {"segments", "parser_versions"}}
            | {
                "pdf_page_count": entry["pages"],
                "local_source_present": True,
                "public_redistribution": "hold",
                "full_pdf_publication": "hold",
                "reviewer": "Codex technical bibliographic/resource review; "
                "independent human pending",
                "third_party_assets": "not individually cleared; exclude full PDFs and images",
                "article_vs_asset_note": (
                    "D001 official published version is CC-BY-NC-ND-4.0; this local layout "
                    "differs, no derivative public permission inferred"
                )
                if code == "D001"
                else None,
                "publication_note": "online 2022; journal issue 2023" if code == "D129" else None,
            }
        )
    for result in acquisition["articles"]:
        cid = result["candidate_id"]
        path = root / "assets" / cid
        xml_asset = next(
            (
                a
                for a in result.get("assets", [])
                if a["format"] == "xml" and a["status"] == "downloaded"
            ),
            None,
        )
        if xml_asset is None:
            new_audits.append(
                {"document_id": cid, "status": "BLOCKED", "reason": "missing verified JATS"}
            )
            continue
        article = article_xml(path / "article.xml", result["expected_doi"])
        pdf_path = path / "article.pdf"
        pdf = PdfReader(pdf_path)
        first = "".join((pdf.pages[0].extract_text() or "").split()).casefold()
        if result["expected_doi"].casefold() not in first:
            raise ValueError("PDF DOI not verified: " + cid)
        pdf_all = "\n".join(p.extract_text() or "" for p in pdf.pages)
        credit_lines = [
            line.strip()
            for line in pdf_all.splitlines()
            if re.search(r"reproduced|adapted from|with permission|courtesy|photo:", line, re.I)
        ]
        info = {
            "document_id": cid,
            "work_id": "doi:" + article["doi"].casefold(),
            "doi": article["doi"],
            "pmcid": article["pmcid"],
            "title": article["title"],
            "authors": article["authors"],
            "source_url": "https://doi.org/" + article["doi"],
            "source_sha256": article["source_sha256"],
            "format": "xml",
            "license": article["license"],
            "license_url": LICENSE_URL,
            "license_evidence": {
                "asset_sha256": article["source_sha256"],
                "xml": article["license_xml"],
                "url": xml_asset["url"],
            },
            "rights_decision": "approved_prose_only",
            "approved_scope": "technical_evaluation_nonclinical",
            "clinical_approval": False,
            "publication_year": new_map[cid]["year"] if cid in new_map else 2023,
            "segments": article["paragraphs"],
            "modifications": "Whitespace normalized; abstract/body paragraphs only; exclude "
            "figures/tables/supplements/references/peer review; "
            "no translation or generated summary",
        }
        public_docs.append(info)
        if cid in new_map:
            all_docs.append(info)
        new_audits.append(
            {k: v for k, v in info.items() if k != "segments"}
            | {
                "status": "DONE",
                "assets": result["assets"],
                "pdf_page_count": len(pdf.pages),
                "pdf_identity_verified": True,
                "pdf_license_statement_present": bool(
                    re.search(r"Creative\s+Commons|CC BY|Attribution License", pdf_all, re.I)
                ),
                "public_derivative": "abstract/body prose only",
                "full_pdf_publication": "hold",
                "full_xml_publication": "hold",
                "third_party_credit_lines": credit_lines,
                "excluded_resource_tags": article["excluded_resources"],
                "article_type": article["article_type"],
                "related_notices_in_asset": article["related_notices"],
                "cloud_retracted": result.get("cloud_metadata", {}).get("is_retracted"),
                "reviewer": "Codex technical resource review; "
                "not independent legal or clinical approval",
            }
        )
    public = build_corpus("medical_public_v1", public_docs, public=True)
    combined = build_corpus("medical_combined_v1", all_docs, public=False)
    # Unknown old rights are local review material; never send their content to external models.
    combined["external_model_allowed_work_ids"] = [d["work_id"] for d in public_docs]
    # Bibliographic corrections use a new release; the first frozen release remains local.
    combined["release"] = "medical_combined_v1-prose-2"
    from rag_portfolio.public_medical import canonical

    combined["corpus_sha256"] = sha(
        canonical({k: v for k, v in combined.items() if k != "corpus_sha256"})
    )
    immutable_json(root / "corpora/medical_public_v1.json", public)
    immutable_json(root / "corpora/medical_combined_v1-prose-2.json", combined)
    report = {
        "schema_version": "rights-review-v1",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "original_eight": originals,
        "collected_assets": new_audits,
        "policy": "Only reviewed CC-BY-4.0 article prose is redistributable in this release; "
        "all raw assets stay ignored; copyright does not grant clinical approval",
        "review_limit": "Codex technical audit; independent human review pending",
    }
    write_json(args.artifacts / "rights_audit.json", report)
    write_json(
        root / "references.json",
        {
            r["candidate_id"]: article_xml(
                root / "assets" / r["candidate_id"] / "article.xml", r["expected_doi"]
            )["references"]
            for r in acquisition["articles"]
            if (root / "assets" / r["candidate_id"] / "article.xml").exists()
        },
    )
    manifest = {
        "schema_version": "medical-corpus-release-v1",
        "base_head": json.loads((root / "legacy-baseline.json").read_text(encoding="utf8"))[
            "base_head"
        ],
        "datasets": [
            {
                "corpus_id": c["corpus_id"],
                "corpus_sha256": c["corpus_sha256"],
                "work_count": len(c["documents"]),
                "chunk_count": len(c["chunks"]),
                "segment_count": len(c["segments"]),
                "release": c["release"],
                "approved_scope": c["approved_scope"],
                "clinical_approval": False,
            }
            for c in [combined, public]
        ],
        "original_pdf_pages": sum(e["pages"] for e in evidence),
        "new_pdf_pages": sum(
            r["pdf_page_count"]
            for r in new_audits
            if r["document_id"] in new_map and r.get("status") == "DONE"
        ),
        "sources": [{k: v for k, v in d.items() if k != "segments"} for d in public_docs],
        "raw_assets_publication": "hold; locally retained with hashes and reacquisition script",
        "combined_full_model_eval": "BLOCKED for old rights-held prose; "
        "local BM25 only until permitted",
        "public_prose_file": "public-prose.json",
    }
    immutable_json(args.artifacts / "public-prose.json", public)
    write_json(args.artifacts / "manifest.json", manifest)
    print(json.dumps(manifest["datasets"]))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--local", type=Path, default=Path(".local/public_medical_corpus_v1"))
    p.add_argument("--artifacts", type=Path, default=Path("artifacts/public-medical-corpus-v1"))
    main(p.parse_args())
