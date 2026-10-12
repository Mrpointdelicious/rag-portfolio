"""Independent medical corpus governance and source coordinates; no clinical approval.

The public release contains reviewed article prose only. Original PDFs, XML,
figures, tables, supplements, personal data and vectors never enter this package.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from rag_portfolio.services.hybrid_contract import Snippet

PARSER = "jats-prose-v1:pdf-conservative-v1:c1100:o120"
EXCLUDED = {"fig", "table-wrap", "supplementary-material", "app", "ref-list", "ack", "sub-article"}
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")


def immutable_json(path, value):
    if path.exists():
        if json.loads(path.read_text(encoding="utf8")) != value:
            raise ValueError("Frozen artifact changed; choose a new version")
        return
    write_json(path, value)


def article_xml(path: Path, expected_doi: str) -> dict:
    raw = path.read_bytes()
    if b"<!ENTITY" in raw.upper():
        raise ValueError("XML entities are not allowed")
    root = ET.fromstring(raw)
    if root.tag != "article" or root.get("article-type") in {
        "retraction",
        "correction",
        "expression-of-concern",
    }:
        raise ValueError("Expected original article; notice requires manual review")
    meta = root.find("front/article-meta")
    ids = {e.get("pub-id-type"): "".join(e.itertext()).strip() for e in meta.findall("article-id")}
    if ids.get("doi", "").casefold() != expected_doi.casefold():
        raise ValueError("Article DOI mismatch")
    license_node = meta.find("permissions/license")
    license_xml = ET.tostring(license_node, encoding="unicode") if license_node is not None else ""
    approved = bool(re.search(r"https?://creativecommons.org/licenses/by/4\.0/?", license_xml))
    parents = {child: parent for parent in root.iter() for child in parent}
    paths = {root: "/article"}
    for parent in root.iter():
        counts = {}
        for child in parent:
            counts[child.tag] = counts.get(child.tag, 0) + 1
            paths[child] = f"{paths[parent]}/{child.tag}[{counts[child.tag]}]"
    paragraphs = []
    credits = []
    for element in root.iter():
        if element.tag in {"attrib", "copyright-statement", "permissions"}:
            text = " ".join("".join(element.itertext()).split())
            if text:
                credits.append({"xpath": paths[element], "text": text})
        if element.tag != "p":
            continue
        chain = []
        current = element
        while current in parents:
            current = parents[current]
            chain.append(current)
        tags = {p.tag for p in chain}
        if tags & EXCLUDED or not ("body" in tags or "abstract" in tags) or "p" in tags:
            continue
        text = " ".join("".join(element.itertext()).split())
        if not text:
            continue
        section = next(
            (p.findtext("title") for p in chain if p.tag == "sec" and p.findtext("title")),
            "Abstract",
        )
        paragraphs.append(
            {
                "text": text,
                "page": None,
                "section": section,
                "locator": paths[element],
                "coordinate_system": "normalized_jats_paragraph",
            }
        )
    names = [
        " ".join("".join(n.itertext()).split()) for n in meta.findall("contrib-group/contrib/name")
    ]
    references = []
    for ref in root.findall(".//ref-list/ref"):
        references.append(
            {
                "id": ref.get("id"),
                "text": " ".join("".join(ref.itertext()).split()),
                "dois": [
                    "".join(d.itertext()).lower()
                    for d in ref.findall(".//pub-id[@pub-id-type='doi']")
                ],
            }
        )
    title = " ".join("".join(meta.find("title-group/article-title").itertext()).split())
    return {
        "title": title,
        "doi": ids["doi"],
        "pmcid": ids.get("pmc", ids.get("pmcid")),
        "authors": names,
        "source_sha256": sha(raw),
        "license": "CC-BY-4.0" if approved else "unknown",
        "license_xml": license_xml,
        "credits": credits,
        "paragraphs": paragraphs,
        "references": references,
        "article_type": root.get("article-type"),
        "related_notices": [
            ET.tostring(e, encoding="unicode") for e in meta.findall("related-article")
        ],
        "excluded_resources": sorted(EXCLUDED),
    }


def pdf_segments(path: Path, *, parser_override=None, text_regions=None):
    # Reuse the existing parser without writing its frozen v1/v2/v3 directories.
    from rag_portfolio.evaluation.medical_corpus import extract_pdf

    pages, versions = extract_pdf(path, parser_override=parser_override, text_regions=text_regions)
    return [
        {
            "text": p["text"],
            "page": p["pdf_page"],
            "section": "PDF page " + str(p["pdf_page"]),
            "locator": f"pdf-physical-page:{p['pdf_page']}",
            "coordinate_system": "normalized_pdf_page",
            "quality_flags": p["quality_flags"],
        }
        for p in pages
    ], versions


def public_gate(document: dict) -> None:
    if (
        document["license"] != "CC-BY-4.0"
        or document.get("rights_decision") != "approved_prose_only"
    ):
        raise ValueError("Public release requires reviewed resource-level prose rights")
    if (
        document.get("clinical_approval") is not False
        or document.get("approved_scope") != "technical_evaluation_nonclinical"
    ):
        raise ValueError("Clinical authorization cannot be inferred from copyright")
    if not document.get("authors") or not document.get("license_evidence"):
        raise ValueError("Attribution and license evidence required")


def build_corpus(corpus_id: str, documents: list[dict], *, public: bool) -> dict:
    if corpus_id not in {"medical_combined_v1", "medical_public_v1"}:
        raise ValueError("Independent corpus identifier required")
    if len({d["work_id"] for d in documents}) != len(documents):
        raise ValueError("Duplicate work; file hashes do not define independent studies")
    chunks, segments, metadata = [], [], []
    for document in documents:
        if public:
            public_gate(document)
        info = {k: v for k, v in document.items() if k != "segments"}
        metadata.append(info)
        prefix = "public" if public else "combined"
        version = f"{prefix}-v1-{document['format']}-{document['source_sha256'][:12]}"
        source = (
            "https://doi.org/" + document["doi"] if document.get("doi") else document["source_url"]
        )
        for i, segment in enumerate(document["segments"]):
            if segment.get("quality_flags"):
                continue
            segment_id = f"{document['document_id'].lower()}-s{i:04}"
            text = segment["text"]
            segments.append(
                {
                    **segment,
                    "segment_id": segment_id,
                    "document_id": document["document_id"],
                    "text_sha256": sha(text.encode()),
                }
            )
            start = 0
            j = 0
            while start < len(text):
                end = min(start + 1100, len(text))
                if end < len(text):
                    stop = max(
                        text.rfind(". ", start + 550, end),
                        text.rfind("\n", start + 550, end),
                        text.rfind("。", start + 550, end),
                    )
                    if stop > start:
                        end = stop + 1
                piece = text[start:end]
                if not piece.strip():
                    break
                section_id = f"s{i:04}-c{j:03}"
                snippet = Snippet(
                    source_id=uuid5(NAMESPACE_URL, source),
                    chunk_id=uuid5(NAMESPACE_URL, f"{source}@{version}#{section_id}"),
                    doc_ref=f"{document['document_id'].lower()}@{version}#{section_id}",
                    document_version=version,
                    title=document["title"][:200],
                    source=source,
                    section=segment["section"][:200],
                    section_id=section_id,
                    page=segment["page"],
                    start=start,
                    end=end,
                    text=piece,
                    text_sha256=sha(piece.encode()),
                    score=0,
                )
                chunks.append(
                    {
                        **snippet.model_dump(mode="json"),
                        "work_id": document["work_id"],
                        "document_id": document["document_id"],
                        "segment_id": segment_id,
                        "source_sha256": document["source_sha256"],
                        "parser_version": PARSER,
                        "locator": segment["locator"],
                        "coordinate_system": segment["coordinate_system"],
                        "license": document["license"],
                        "clinical_approval": False,
                    }
                )
                if end == len(text):
                    break
                start = end - 120
                j += 1
    payload = {
        "schema_version": "medical-corpus-v1",
        "corpus_id": corpus_id,
        "approved_scope": "technical_evaluation_nonclinical",
        "release": corpus_id + "-prose-1",
        "clinical_approval": False,
        "parser_version": PARSER,
        "documents": metadata,
        "segments": segments,
        "chunks": chunks,
    }
    payload["corpus_sha256"] = sha(canonical(payload))
    validate_corpus(payload)
    return payload


def validate_corpus(corpus):
    if (
        sha(canonical({k: v for k, v in corpus.items() if k != "corpus_sha256"}))
        != corpus["corpus_sha256"]
    ):
        raise ValueError("Corpus fingerprint drift")
    if corpus["corpus_id"] == "medical_public_v1":
        for document in corpus["documents"]:
            public_gate(document)
    segments = {s["segment_id"]: s for s in corpus["segments"]}
    docs = {d["document_id"]: d for d in corpus["documents"]}
    if (
        corpus.get("clinical_approval") is not False
        or corpus.get("approved_scope") != "technical_evaluation_nonclinical"
        or len({d["work_id"] for d in corpus["documents"]}) != len(docs)
    ):
        raise ValueError("Duplicate work or clinical scope escalation")
    if len(segments) != len(corpus["segments"]) or len(docs) != len(corpus["documents"]):
        raise ValueError("Duplicate source coordinates")
    seen = set()
    for segment in segments.values():
        if segment["text_sha256"] != sha(segment["text"].encode()):
            raise ValueError("Source segment fingerprint drift")
    for chunk in corpus["chunks"]:
        snippet = Snippet.model_validate({k: chunk[k] for k in Snippet.model_fields})
        segment = segments[chunk["segment_id"]]
        doc = docs[chunk["document_id"]]
        if (
            snippet.text != segment["text"][snippet.start : snippet.end]
            or chunk["source_sha256"] != doc["source_sha256"]
            or chunk["work_id"] != doc["work_id"]
            or segment["document_id"] != doc["document_id"]
            or chunk["license"] != doc["license"]
            or chunk["clinical_approval"] is not False
            or chunk["locator"] != segment["locator"]
            or chunk["page"] != segment["page"]
            or chunk["coordinate_system"] != segment["coordinate_system"]
            or (
                chunk["coordinate_system"] == "normalized_jats_paragraph"
                and chunk["page"] is not None
            )
            or chunk["chunk_id"] in seen
        ):
            raise ValueError("Invalid source span/format/version")
        seen.add(chunk["chunk_id"])
