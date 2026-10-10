"""Original-PDF pages and deterministic source spans for an offline medical experiment.

This module does not approve documents, write PostgreSQL, or activate a release.
Offsets always refer to the saved normalized page text, with the raw extraction
and its hash retained alongside it. Printed pagination is never guessed.
"""

import hashlib
import json
import re
import tempfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from rag_portfolio.evaluation.experiments import MEDICAL_DOMAIN, MEDICAL_EXPERIMENT
from rag_portfolio.literature_backup import LiteratureError, file_hash, write_json
from rag_portfolio.services.chunking import DEFAULT_CHUNKING_CONFIG, ChunkingConfig

CHUNKER_REVISION = "pdf-page-section-v1:c800:o80"
NORMALIZATION_REVISION = "pdf-conservative-v1"
REVIEWED_PUBLISHER_MARGINS = (
    "上海中医药杂志  2024  年第  58  卷 第 6  期  Shanghai J Tradit Chin Med ， "
    "Vol . 58 ， No . 6 ， Jun .  2024",
    "中国康复理论与实践 2012 年 4 月第 18 卷第 4 期 Chin J Rehabil Theory Pract, "
    "Apr. 2012, Vol. 18, No.4",
    "www.rehabi.com.cn",
    "http://www.cjebm.com",
    "COPYRIGHT© 2021 EDIZIONI MINERVA MEDICA",
)
SECTION_PATTERN = re.compile(
    r"^(?:\d{1,2}(?:[.．]\d{1,2})*[ \t\u3000]+[\u4e00-\u9fff].{0,35}|"
    r"[一二三四五六七八九十]+[、．.]\S.{1,60}|"
    r"前言|引言|Abstract|Introduction|Materials and Methods|Methods|Results|"
    r"Discussion|Conclusions?|References)$",
    re.IGNORECASE,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def quality_flags(text: str) -> list[str]:
    flags = []
    visible = [char for char in text if not char.isspace()]
    if len(visible) < 40:
        flags.append("low_text")
    if "\ufffd" in text:
        flags.append("replacement_characters")
    if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
        flags.append("private_use_characters")
    if re.search(r"\(cid:\d+\)", text):
        flags.append("unmapped_glyphs")
    if "\x00" in text:
        flags.append("nul_characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        flags.append("control_glyphs")
    if any(ord(char) & 0xFFFF in (0xFFFE, 0xFFFF) for char in text):
        flags.append("noncharacter_glyphs")
    return flags


def normalize_pages(
    raw_pages: list[str], *, approved_margins: list[str] | None = None
) -> tuple[list[str], list[dict]]:
    """Only remove reviewed publisher margins; repeated clinical text must survive."""
    margins: Counter[str] = Counter()
    for text in raw_pages:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        margins.update({line for line in lines[:2] + lines[-2:] if 6 <= len(line) <= 100})
    allowed = set(REVIEWED_PUBLISHER_MARGINS if approved_margins is None else approved_margins)
    repeated = {
        line
        for line, count in margins.items()
        if line in allowed and count >= max(3, len(raw_pages) * 0.6)
    }
    texts, logs = [], []
    for raw in raw_pages:
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        nonempty = [i for i, line in enumerate(lines) if line.strip()]
        edge = set(nonempty[:2] + nonempty[-2:])
        removed = [i for i in edge if lines[i].strip() in repeated]
        lines = [line.rstrip() for i, line in enumerate(lines) if i not in removed]
        text = "\n".join(lines).strip()
        # PDFium exposes this document's printed/discretionary hyphen as FFFE.
        # Preserve a visible hyphen rather than guessing whether words should join.
        hyphens = text.count("\ufffe") + text.count("\x02")
        text = text.replace("\ufffe", "-").replace("\x02", "-")
        # Expand typographic ligatures only. No case-folding, unit conversion,
        # negation removal or speculative repair of cross-column reading order.
        for old, new in {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}.items():
            text = text.replace(old, new)
        texts.append(text)
        logs.append(
            {
                "removed_margin_line_indices": sorted(removed),
                "discretionary_hyphen_repairs": hyphens,
                "revision": NORMALIZATION_REVISION,
            }
        )
    return texts, logs


def extract_pdf(
    path: Path, *, parser_override: str | None = None, text_regions: dict | None = None
) -> tuple[list[dict], dict]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pdfium = None
    pages = []
    try:
        for index, page in enumerate(reader.pages):
            try:
                baseline = page.extract_text() or ""
                baseline_flags = quality_flags(baseline)
            except Exception as exc:
                baseline = ""
                baseline_flags = [f"extraction_error:{type(exc).__name__}"]
            chosen, parser = baseline, "pypdf"
            attempted = [
                {"parser": "pypdf", "flags": baseline_flags, "text_sha256": text_digest(baseline)}
            ]
            if baseline_flags or parser_override == "pdfium":
                import pypdfium2

                if pdfium is None:
                    pdfium = pypdfium2.PdfDocument(str(path))
                alternate_page = pdfium[index]
                try:
                    text_page = alternate_page.get_textpage()
                    try:
                        regions = (text_regions or {}).get(str(index + 1))
                        if regions:
                            width, height = alternate_page.get_size()
                            alternate = "\n\n".join(
                                text_page.get_text_bounded(
                                    left=left * width,
                                    bottom=bottom * height,
                                    right=right * width,
                                    top=top * height,
                                )
                                for left, bottom, right, top in regions
                            )
                        else:
                            alternate = text_page.get_text_range()
                    finally:
                        text_page.close()
                finally:
                    alternate_page.close()
                alternate_flags = quality_flags(alternate)
                attempted.append(
                    {
                        "parser": "pdfium",
                        "flags": alternate_flags,
                        "text_sha256": text_digest(alternate),
                    }
                )
                if parser_override == "pdfium" or len(alternate_flags) < len(baseline_flags):
                    chosen, parser = alternate, "pdfium"
            pages.append(
                {
                    "pdf_page": index + 1,
                    "printed_page": None,
                    "parser": parser,
                    "parser_version": version("pypdf" if parser == "pypdf" else "pypdfium2"),
                    "raw_text": chosen,
                    "raw_text_sha256": text_digest(chosen),
                    "quality_flags": quality_flags(chosen),
                    "parser_attempts": attempted,
                    "text_regions": (text_regions or {}).get(str(index + 1)),
                }
            )
    finally:
        if pdfium is not None:
            pdfium.close()
    normalized, logs = normalize_pages([page["raw_text"] for page in pages])
    for page, text, log in zip(pages, normalized, logs, strict=True):
        page.update(
            raw_quality_flags=page["quality_flags"],
            quality_flags=quality_flags(text),
            text=text,
            text_sha256=text_digest(text),
            normalization=log,
        )
    return pages, {"pypdf": version("pypdf"), "pypdfium2": version("pypdfium2")}


def page_chunks(
    page: dict, document: dict, *, config: ChunkingConfig = DEFAULT_CHUNKING_CONFIG
) -> list[dict]:
    """Use the existing 800/80 boundary rule inside a page and detected section."""
    text = page["text"]
    boundaries = [(0, f"PDF page {page['pdf_page']}")]
    offset = 0
    for line in text.splitlines(keepends=True):
        heading = line.strip()
        if SECTION_PATTERN.fullmatch(heading):
            if offset == 0:
                boundaries[0] = (0, heading)
            else:
                boundaries.append((offset, heading))
        offset += len(line)
    result = []
    for section_index, (section_start, section) in enumerate(boundaries):
        section_end = (
            boundaries[section_index + 1][0] if section_index + 1 < len(boundaries) else len(text)
        )
        start = section_start
        while start < section_end:
            end = min(start + config.max_chars, section_end)
            if end < section_end:
                lower = start + config.max_chars // 2
                boundary = text.rfind("\n\n", lower, end)
                if boundary < 0:
                    boundary = text.rfind("\n", lower, end)
                if boundary >= 0:
                    end = boundary + 1
            raw = text[start:end]
            value = raw.strip("\n")
            actual_start = start + len(raw) - len(raw.lstrip("\n"))
            actual_end = actual_start + len(value)
            if value.strip():
                identity = {
                    "domain": MEDICAL_DOMAIN,
                    "document_id": document["document_id"],
                    "source_sha256": document["sha256"],
                    "page": page["pdf_page"],
                    "parser": page["parser"],
                    "parser_version": page["parser_version"],
                    "page_text_sha256": page["text_sha256"],
                    "section": section,
                    "start": actual_start,
                    "end": actual_end,
                    "chunker": CHUNKER_REVISION,
                }
                result.append(
                    {
                        "chunk_id": str(uuid5(NAMESPACE_URL, digest(identity))),
                        "document_id": document["document_id"],
                        "title": document["title"],
                        "source_sha256": document["sha256"],
                        "source_path": document["path"],
                        "source_type": "original_pdf",
                        "domain": MEDICAL_DOMAIN,
                        "pdf_page_start": page["pdf_page"],
                        "pdf_page_end": page["pdf_page"],
                        "printed_page": page["printed_page"],
                        "section_path": section,
                        "start_char": actual_start,
                        "end_char": actual_end,
                        "text": value,
                        "text_sha256": text_digest(value),
                        "page_text_sha256": page["text_sha256"],
                        "parser": page["parser"],
                        "parser_version": page["parser_version"],
                        "chunker_revision": CHUNKER_REVISION,
                        "metadata": document["metadata"],
                    }
                )
            if end >= section_end:
                break
            next_start = max(start + 1, end - config.overlap_chars)
            start = next_start
    return result


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _build_corpus(backup_root: Path, sources: dict, output: Path) -> dict:
    if (
        sources.get("domain") != MEDICAL_DOMAIN
        or sources.get("experiment_id") != MEDICAL_EXPERIMENT
    ):
        raise LiteratureError("Not a medical source configuration")
    backup = json.loads((backup_root / "backup_manifest.json").read_text(encoding="utf-8"))
    entries = {row["document_id"]: row for row in backup["documents"]}
    source_rows = sources["documents"]
    keys = [row["document_id"] for row in source_rows]
    if len(set(keys)) != len(keys) or set(keys) != {
        key for key, row in entries.items() if row["selected"]
    }:
        raise LiteratureError("Medical sources must match the explicit selected-PDF backup set")
    pages, chunks, documents = [], [], []
    output.mkdir(parents=True, exist_ok=True)
    for source in source_rows:
        entry = entries[source["document_id"]]
        path = (backup_root / entry["path"]).resolve()
        if (
            not path.is_relative_to((backup_root / "selected").resolve())
            or path.suffix.lower() != ".pdf"
        ):
            raise LiteratureError("Only selected original PDFs may enter this corpus")
        if file_hash(path) != source["sha256"] or source["sha256"] != entry["sha256"]:
            raise LiteratureError("Selected PDF differs from its frozen source hash")
        document = {**entry, "metadata": source["metadata"]}
        extracted, parser_versions = extract_pdf(
            path,
            parser_override=source.get("parser_override"),
            text_regions=source.get("text_regions"),
        )
        if len(extracted) != entry["pdf_pages"]:
            raise LiteratureError("Page count differs from the inventory")
        document_chunks = []
        for page in extracted:
            page.update(document_id=entry["document_id"], source_sha256=entry["sha256"])
            page["printed_page"] = source.get("printed_pages", {}).get(str(page["pdf_page"]))
            pages.append(page)
            if not page["quality_flags"]:
                document_chunks.extend(page_chunks(page, document))
        for ordinal, chunk in enumerate(document_chunks):
            chunk["ordinal"] = ordinal
        chunks.extend(document_chunks)
        documents.append(
            {
                "document_id": entry["document_id"],
                "title": entry["title"],
                "source_sha256": entry["sha256"],
                "source_path": entry["path"],
                "metadata": source["metadata"],
                "page_count": len(extracted),
                "chunk_count": len(document_chunks),
                "parser_versions": parser_versions,
                "review_status": "not_approved",
                "work_id": source.get("work_id"),
                "identity_status": source.get("identity_status", "inventory_candidate"),
            }
        )
        markdown = f"# {entry['title']}\n\n" + "\n\n".join(
            f"## PDF page {page['pdf_page']}\n\n{page['text']}" for page in extracted
        )
        (output / f"{entry['document_id']}.normalized.md").write_text(
            markdown + "\n", encoding="utf-8"
        )
    if not chunks:
        raise LiteratureError("Empty medical corpus")
    _write_jsonl(output / "pages.jsonl", pages)
    _write_jsonl(output / "chunks.jsonl", chunks)
    artifact_names = [
        "pages.jsonl",
        "chunks.jsonl",
        *[f"{row['document_id']}.normalized.md" for row in documents],
    ]
    payload = {
        "schema_version": 1,
        "domain": MEDICAL_DOMAIN,
        "experiment_id": MEDICAL_EXPERIMENT,
        "purpose": "offline_retrieval_experiment",
        "source_type": "original_pdf",
        "sources_sha256": digest(sources),
        "chunker_revision": CHUNKER_REVISION,
        "normalization_revision": NORMALIZATION_REVISION,
        "documents": documents,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "flagged_pages": [
            {
                "document_id": row["document_id"],
                "pdf_page": row["pdf_page"],
                "flags": row["quality_flags"],
            }
            for row in pages
            if row["quality_flags"]
        ],
        "artifacts": {name: file_hash(output / name) for name in artifact_names},
    }
    payload["corpus_fingerprint"] = digest(payload)
    write_json(output / "manifest.json", payload)
    return payload


def prepare_corpus(backup_root: Path, sources: dict, output: Path) -> dict:
    """Stage first: a failed re-extraction cannot corrupt a previously frozen corpus."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="prepare-") as directory:
        staging = Path(directory)
        payload = _build_corpus(backup_root, sources, staging)
        if output.exists():
            if not (output / "manifest.json").is_file():
                raise LiteratureError("Existing output is not a frozen corpus")
            existing, _, _ = load_corpus(output / "manifest.json", backup_root)
            if existing != payload:
                raise LiteratureError("Existing corpus changed; use a new revision directory")
        else:
            staging.replace(output)
        return payload


def load_corpus(path: Path, backup_root: Path) -> tuple[dict, list[dict], list[dict]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("domain") != MEDICAL_DOMAIN
        or manifest.get("experiment_id") != MEDICAL_EXPERIMENT
    ):
        raise LiteratureError("Corpus belongs to another domain or experiment")
    if (
        manifest.get("purpose") != "offline_retrieval_experiment"
        or manifest.get("source_type") != "original_pdf"
    ):
        raise LiteratureError("Expected the original-PDF offline experiment")
    if (
        digest({key: value for key, value in manifest.items() if key != "corpus_fingerprint"})
        != manifest["corpus_fingerprint"]
    ):
        raise LiteratureError("Corpus manifest drift")
    for name, expected in manifest["artifacts"].items():
        artifact = (path.parent / name).resolve()
        if not artifact.is_relative_to(path.parent.resolve()) or file_hash(artifact) != expected:
            raise LiteratureError("Corpus artifact drift")
    for document in manifest["documents"]:
        original = (backup_root / document["source_path"]).resolve()
        if (
            not original.is_relative_to((backup_root / "selected").resolve())
            or file_hash(original) != document["source_sha256"]
        ):
            raise LiteratureError("Original-PDF backup drift")
    pages = [
        json.loads(line)
        for line in (path.parent / "pages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    chunks = [
        json.loads(line)
        for line in (path.parent / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    page_map = {(row["document_id"], row["pdf_page"]): row for row in pages}
    if len(page_map) != len(pages) or len({row["chunk_id"] for row in chunks}) != len(chunks):
        raise LiteratureError("Duplicate page or chunk identity")
    for chunk in chunks:
        page = page_map[(chunk["document_id"], chunk["pdf_page_start"])]
        if (
            chunk["domain"] != MEDICAL_DOMAIN
            or chunk["source_type"] != "original_pdf"
            or chunk["pdf_page_start"] != chunk["pdf_page_end"]
            or chunk["source_sha256"] != page["source_sha256"]
            or chunk["page_text_sha256"] != text_digest(page["text"])
            or page["text"][chunk["start_char"] : chunk["end_char"]] != chunk["text"]
            or text_digest(chunk["text"]) != chunk["text_sha256"]
        ):
            raise LiteratureError("Chunk-to-original-page mapping is invalid")
    if len(pages) != manifest["page_count"] or len(chunks) != manifest["chunk_count"]:
        raise LiteratureError("Corpus counts differ")
    return manifest, pages, chunks
