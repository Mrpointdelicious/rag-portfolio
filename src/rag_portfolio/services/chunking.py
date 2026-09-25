"""Small, deterministic UTF-8 Markdown/TXT parser and character-based chunker.

This module performs no file, database or model calls. Token counts are estimates;
replace them with the embedding model's tokenizer when indexing is implemented.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

DocumentFormat = Literal["markdown", "text"]
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_CHUNKS = 4096
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ESTIMATED_TOKEN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)


@dataclass(frozen=True)
class ParsedDocument:
    filename: str
    format: DocumentFormat
    text: str
    content_hash: str


@dataclass(frozen=True)
class ChunkingConfig:
    max_chars: int = 800
    overlap_chars: int = 80

    def __post_init__(self) -> None:
        if type(self.max_chars) is not int or type(self.overlap_chars) is not int:
            raise ValueError("Chunk sizes must be integers")
        if not 128 <= self.max_chars <= 8192:
            raise ValueError("max_chars must be between 128 and 8192")
        if not 0 <= self.overlap_chars < self.max_chars // 2:
            raise ValueError("overlap_chars must be less than half of max_chars")

    def version(self, document_format: DocumentFormat) -> str:
        return f"simple-v1:{document_format}:c{self.max_chars}:o{self.overlap_chars}"


DEFAULT_CHUNKING_CONFIG = ChunkingConfig()


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    section_path: str
    text: str
    content_hash: str
    token_count: int


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_document(filename: str, content: str | bytes) -> ParsedDocument:
    """Normalize line endings without removing Markdown indentation/hard breaks."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in {".md", ".markdown", ".txt"}:
        raise ValueError("Only .md, .markdown and .txt documents are supported")
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 2 MiB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Document must be UTF-8 encoded") from exc
    if "\x00" in text or not text.strip():
        raise ValueError("Document must contain nonempty text without NUL characters")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n") + "\n"
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("Normalized document exceeds the 2 MiB limit")
    document_format: DocumentFormat = "text" if suffix == ".txt" else "markdown"
    return ParsedDocument(name, document_format, text, text_hash(text))


def _sections(text: str, document_format: DocumentFormat) -> list[tuple[str, str]]:
    if document_format == "text":
        return [("", text)]
    sections: list[tuple[str, str]] = []
    headings: list[tuple[int, str]] = []
    lines: list[str] = []
    path = ""
    fence_char = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        fence = FENCE.match(line.rstrip("\n"))
        if fence_char:
            lines.append(line)
            if (
                fence
                and fence.group(1)[0] == fence_char
                and len(fence.group(1)) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_char = ""
            continue
        if fence:
            fence_char, fence_length = fence.group(1)[0], len(fence.group(1))
            lines.append(line)
            continue
        heading = HEADING.match(line.rstrip("\n"))
        if heading:
            if "".join(lines).strip():
                sections.append((path, "".join(lines)))
            lines = []
            level = len(heading.group(1))
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2))
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            path = " / ".join(title for _, title in headings)
        lines.append(line)
    if "".join(lines).strip():
        sections.append((path, "".join(lines)))
    return sections


def chunk_text(
    text: str,
    document_format: DocumentFormat,
    config: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
) -> list[Chunk]:
    """Split within sections, preferring paragraphs/lines over hard character cuts.

    ATX headings outside fenced code determine the section path. Large tables and
    code blocks may span chunks; this is deliberately not a full Markdown parser.
    """
    if document_format not in {"markdown", "text"}:
        raise ValueError("Unsupported document format")
    if not text.strip() or "\x00" in text:
        raise ValueError("Cannot chunk empty or binary text")
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 2 MiB limit")
    chunks: list[Chunk] = []
    for section_path, section in _sections(text, document_format):
        start = 0
        while start < len(section):
            end = min(start + config.max_chars, len(section))
            if end < len(section):
                lower = start + config.max_chars // 2
                boundary = section.rfind("\n\n", lower, end)
                if boundary < 0:
                    boundary = section.rfind("\n", lower, end)
                if boundary >= 0:
                    end = boundary + 1
            value = section[start:end].strip("\n")
            if value.strip():
                if len(chunks) >= MAX_CHUNKS:
                    raise ValueError("Document exceeds the 4096 chunk limit")
                chunks.append(
                    Chunk(
                        ordinal=len(chunks),
                        section_path=section_path,
                        text=value,
                        content_hash=text_hash(value),
                        token_count=len(ESTIMATED_TOKEN.findall(value)),
                    )
                )
            if end == len(section):
                break
            start = end - config.overlap_chars
    return chunks
