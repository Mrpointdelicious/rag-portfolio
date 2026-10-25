"""Docling provenance and LlamaIndex transformations used by the main ingestion worker."""

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rag_portfolio.config import Settings

ADAPTER_REVISION = "docling-llama-v2"
TOKEN_COUNTER = "cl100k_base"


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding(TOKEN_COUNTER)


def token_ids(text: str) -> list[int]:
    """Explicit reference BPE counter; not a claim about every configured answer model."""
    return _encoding().encode(text, disallowed_special=())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def asset_path(settings: Settings, key: str) -> Path:
    root = Path(settings.asset_root).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("invalid_asset_key")
    return path


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    section: str
    provenance: dict


@dataclass(frozen=True)
class ParsedSource:
    text: str
    chunks: list[ParsedChunk]
    revision: str
    cache_hit: bool


def parse_source(settings: Settings, path: Path, filename: str, source_sha: str) -> ParsedSource:
    """Blocking parser; callers run it in a worker thread. Never silently substitute a parser."""
    from llama_index.core import Document
    from llama_index.core.ingestion import IngestionPipeline
    from llama_index.core.node_parser import SentenceSplitter

    if digest(path.read_bytes()) != source_sha:
        raise ValueError("source_hash_mismatch")
    is_pdf = filename.lower().endswith(".pdf")
    parser_config = {
        "adapter": ADAPTER_REVISION,
        "parser": "docling" if is_pdf else "utf8",
        "ocr": settings.docling_ocr if is_pdf else False,
        "tables": settings.docling_tables if is_pdf else False,
    }
    if is_pdf:
        parser_config.update(
            {
                "docling": importlib.metadata.version("docling"),
                "docling_core": importlib.metadata.version("docling-core"),
                "model_revision": settings.docling_model_revision,
            }
        )
    config = {
        "parser_config": parser_config,
        "llama_index": importlib.metadata.version("llama-index-core"),
        "chunk_size": settings.ingestion_chunk_size,
        "overlap": settings.ingestion_chunk_overlap,
        "tokenizer": TOKEN_COUNTER,
    }
    if is_pdf:
        config["node_parser"] = importlib.metadata.version("llama-index-node-parser-docling")
    revision = "llama-v2:" + digest(canonical(config))[:24]
    cache = asset_path(settings, f"parsed/{source_sha}/{revision.replace(':', '-')}.json")
    if cache.exists():
        saved = json.loads(cache.read_bytes())
        if saved.get("source_sha") != source_sha or saved.get("config") != config:
            raise ValueError("parse_cache_mismatch")
        chunks = [ParsedChunk(**chunk) for chunk in saved["chunks"]]
        _validate_chunks(chunks, is_pdf)
        return ParsedSource(saved["text"], chunks, revision, True)
    splitter = SentenceSplitter(
        chunk_size=settings.ingestion_chunk_size,
        chunk_overlap=settings.ingestion_chunk_overlap,
        tokenizer=token_ids,
    )
    if is_pdf:
        from docling_core.types.doc import DoclingDocument
        from llama_index.node_parser.docling import DoclingNodeParser

        parse_key = digest(canonical(parser_config))
        parse_cache = asset_path(settings, f"parsed/{source_sha}/docling-{parse_key}.json")
        if parse_cache.exists():
            saved = json.loads(parse_cache.read_bytes())
            if saved.get("source_sha") != source_sha or saved.get("config") != parser_config:
                raise ValueError("document_cache_mismatch")
            parsed_document = DoclingDocument.model_validate(saved["document"])
        else:
            parsed_document = _convert_pdf(settings, path)
            atomic_write(
                parse_cache,
                canonical(
                    {
                        "source_sha": source_sha,
                        "config": parser_config,
                        "document": parsed_document.model_dump(mode="json"),
                    }
                ),
            )
        parsed_json = parsed_document.model_dump_json()
        normalized_text = parsed_document.export_to_markdown()
        pipeline = IngestionPipeline(transformations=[DoclingNodeParser()])
        parents = pipeline.run(documents=[Document(text=parsed_json, id_=source_sha)])
    else:
        raw = path.read_text(encoding="utf-8-sig")
        normalized_text = raw.replace("\r\n", "\n")
        parents = [Document(text=normalized_text, id_=source_sha)]
    chunks = []
    for parent in parents:
        anchors = []
        for item in parent.metadata.get("doc_items", []):
            for position in item.get("prov", []):
                anchors.append(
                    {
                        "item_ref": item.get("self_ref"),
                        "page": position["page_no"],
                        "bbox": position.get("bbox"),
                        "charspan": position.get("charspan"),
                    }
                )
        section = " / ".join(parent.metadata.get("headings", []))
        labels = sorted(
            {item.get("label", "unknown") for item in parent.metadata.get("doc_items", [])}
        )
        is_table = "table" in labels
        # Keep table rows atomic; a very large row can exceed the context budget and be skipped.
        pieces = (
            _split_table(parent.text, settings.ingestion_chunk_size)
            if is_table
            else splitter.split_text(parent.text)
        )
        for text in pieces:
            chunks.append(
                ParsedChunk(
                    text,
                    section,
                    {
                        "anchors": anchors,
                        "granularity": "element" if is_pdf else "document",
                        "parser_revision": revision,
                        "source_sha256": source_sha,
                        "element_labels": labels,
                        "split_unit": "table_row" if is_table else "sentence",
                    },
                )
            )
    _validate_chunks(chunks, is_pdf)
    atomic_write(
        cache,
        canonical(
            {
                "source_sha": source_sha,
                "config": config,
                "text": normalized_text,
                "chunks": [vars(chunk) for chunk in chunks],
            }
        ),
    )
    return ParsedSource(normalized_text, chunks, revision, False)


def _convert_pdf(settings: Settings, path: Path):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(
        do_ocr=settings.docling_ocr, do_table_structure=settings.docling_tables
    )
    options.layout_options.model_spec = options.layout_options.model_spec.model_copy(
        update={"revision": settings.docling_model_revision}
    )
    options.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.CPU, num_threads=settings.docling_threads
    )
    if settings.docling_artifacts_path:
        options.artifacts_path = Path(settings.docling_artifacts_path)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    result = converter.convert(source=path, raises_on_error=True)
    if result.status != ConversionStatus.SUCCESS:
        raise ValueError("docling_conversion_failed")
    return result.document


def _split_table(text: str, budget: int) -> list[str]:
    lines = text.splitlines()
    # Docling serializes tables as Markdown. Repeat the header across chunks.
    separator = next((i for i, line in enumerate(lines) if "|" in line and "---" in line), None)
    if separator is None:
        return [text]
    header, rows = lines[: separator + 1], lines[separator + 1 :]
    chunks, current = [], list(header)
    for row in rows:
        if len(current) > len(header) and len(token_ids("\n".join([*current, row]))) > budget:
            chunks.append("\n".join(current))
            current = list(header)
        current.append(row)
    if len(current) > len(header) or not chunks:
        chunks.append("\n".join(current))
    return chunks


def _validate_chunks(chunks: list[ParsedChunk], is_pdf: bool):
    if not chunks or len(chunks) > 20000 or any(not chunk.text.strip() for chunk in chunks):
        raise ValueError("invalid_parsed_chunks")
    if is_pdf and any(not chunk.provenance.get("anchors") for chunk in chunks):
        raise ValueError("pdf_missing_source_anchors")
