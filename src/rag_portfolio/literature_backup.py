"""Verified, PDF-only backup of an inventory; never modifies source files or ZIPs."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from contextlib import suppress
from pathlib import Path


class LiteratureError(ValueError):
    """Source integrity or experiment isolation could not be established."""


def inventory_from_workbook(path: Path) -> dict:
    """Read the supplied inventory's plain OOXML cells without spreadsheet dependencies."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns):
                strings.append("".join(item.itertext()))
        relationships = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
        result = {}
        for sheet in sheets:
            if sheet.attrib["name"] not in {"资料清单", "来源位置", "首批试验", "评测候选题"}:
                continue
            target = relationships[sheet.attrib[relationship]]
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            rows = {}
            for row in ET.fromstring(archive.read(member)).findall("s:sheetData/s:row", ns):
                values = {}
                for cell in row.findall("s:c", ns):
                    column = re.match(r"[A-Z]+", cell.attrib["r"])[0]
                    node = cell.find("s:v", ns)
                    if cell.attrib.get("t") == "inlineStr":
                        value = "".join(cell.find("s:is", ns).itertext())
                    elif node is None:
                        value = None
                    elif cell.attrib.get("t") == "s":
                        value = strings[int(node.text)]
                    elif cell.attrib.get("t") in {"str", "e"}:
                        value = node.text
                    else:
                        number = float(node.text)
                        value = int(number) if number.is_integer() else number
                    values[column] = value
                rows[int(row.attrib["r"])] = values
            headers = rows[5]
            result[sheet.attrib["name"]] = [
                {label: row.get(column) for column, label in headers.items() if label}
                for index, row in rows.items()
                if index >= 6 and row.get("A")
            ]
    if result.keys() != {"资料清单", "来源位置", "首批试验", "评测候选题"}:
        raise LiteratureError("Workbook is missing required inventory sheets")
    return {
        "schema_version": 1,
        "domain": "medical_knowledge",
        "inventory_path": str(path.resolve()),
        "inventory_sha256": file_hash(path),
        "documents": result["资料清单"],
        "locations": result["来源位置"],
        "selected": result["首批试验"],
        "candidate_questions": result["评测候选题"],
    }


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as stream:
        temporary = Path(stream.name)
        stream.write(payload.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _member_name(name: str) -> set[str]:
    names = {name.replace("\\", "/")}
    for encoding in ("gbk", "utf-8"):
        with suppress(UnicodeEncodeError, UnicodeDecodeError):
            names.add(name.encode("cp437").decode(encoding).replace("\\", "/"))
    return names


def _copy_source(location: dict, target: Path, expected_hash: str) -> bool:
    source = Path(location["原始路径"])
    if not source.is_file():
        raise LiteratureError(f"Missing source for {location['副本ID']}")
    if target.exists() and file_hash(target) != expected_hash:
        raise LiteratureError(f"Existing backup differs: {target.name}")
    member = location.get("ZIP成员路径")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".part") as out:
        temporary = Path(out.name)
        try:
            if member:
                with zipfile.ZipFile(source) as archive:
                    matches = [
                        item
                        for item in archive.infolist()
                        if not item.is_dir()
                        and member.replace("\\", "/") in _member_name(item.filename)
                    ]
                    if len(matches) != 1:
                        raise LiteratureError(
                            f"ZIP member is missing or ambiguous: {location['副本ID']}"
                        )
                    with archive.open(matches[0]) as incoming:
                        shutil.copyfileobj(incoming, out)
            else:
                with source.open("rb") as incoming:
                    shutil.copyfileobj(incoming, out)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if file_hash(temporary) != expected_hash:
            raise LiteratureError(f"Source hash changed: {location['副本ID']}")
        if location.get("字节数") is not None and temporary.stat().st_size != location["字节数"]:
            raise LiteratureError(f"Source size changed: {location['副本ID']}")
        created = not target.exists()
        if created:
            temporary.replace(target)
        if file_hash(target) != expected_hash:
            raise LiteratureError(f"Backup verification failed: {target.name}")
        return created
    finally:
        temporary.unlink(missing_ok=True)


def backup_pdfs(inventory: dict, destination: Path) -> dict:
    if inventory.get("domain") != "medical_knowledge":
        raise LiteratureError("Expected the independent medical inventory")
    documents = {row["资料ID"]: row for row in inventory["documents"] if row["格式"] == ".pdf"}
    selected = {row["资料ID"] for row in inventory["selected"]}
    if not selected <= documents.keys():
        raise LiteratureError("Selected inventory contains a non-PDF or missing document")
    locations = [row for row in inventory["locations"] if row["格式"] == ".pdf"]
    if len({row["副本ID"] for row in locations}) != len(locations):
        raise LiteratureError("Duplicate source location IDs")
    if {row["资料ID"] for row in locations} != documents.keys():
        raise LiteratureError("Every PDF must have an inventoried source location")
    entries, copied = {}, 0
    for document_id, document in documents.items():
        if not re.fullmatch(r"D\d{3}", document_id):
            raise LiteratureError("Invalid inventory document ID")
        digest = document["SHA256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise LiteratureError("Invalid inventory hash")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", document["标题"]).strip(" .")[:90]
        group = "selected" if document_id in selected else "unselected"
        relative = f"{group}/{document_id}__{title}__{digest[:12]}.pdf"
        entries[document_id] = {
            "document_id": document_id,
            "title": document["标题"],
            "sha256": digest,
            "path": relative,
            "selected": document_id in selected,
            "pdf_pages": document["PDF页数"],
            "publication_year": document.get("发表年份"),
            "edition": document.get("版次"),
            "doi": document.get("DOI"),
            "work_id": None,
            "identity_status": "inventory_candidate",
            "review_status": "not_approved",
            "aliases": [],
        }
    for location in locations:
        entry = entries[location["资料ID"]]
        if location["文件SHA256"] != entry["sha256"]:
            raise LiteratureError("Document and location hashes disagree")
        copied += _copy_source(location, destination / entry["path"], entry["sha256"])
        entry["aliases"].append(location)
    hashes = [entry["sha256"] for entry in entries.values()]
    if len(set(hashes)) != len(hashes):
        raise LiteratureError("Inventory must use one identity per binary PDF")
    manifest = {
        "schema_version": 1,
        "domain": "medical_knowledge",
        "status": "verified",
        "inventory_sha256": inventory["inventory_sha256"],
        "pdf_count": len(entries),
        "source_location_count": len(locations),
        "selected_count": len(selected),
        "unselected_count": len(entries) - len(selected),
        "documents": list(entries.values()),
    }
    write_json(destination / "backup_manifest.json", manifest)
    lines = [
        "# 原始 PDF 备份索引",
        "",
        f"已校验 {len(entries)} 份不同 PDF，保留 {len(locations)} 个来源位置与副本别名。",
        "",
        "仅备份原始 PDF。首批候选放 selected，其余资料放 unselected；备份不代表批准入库。",
        "",
        "| 资料 ID | 分组 | 标题 | 页数 | 文件 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for entry in entries.values():
        title = entry["title"].replace("|", "\\|")
        group = "首批已选" if entry["selected"] else "其余未选"
        lines.append(
            f"| {entry['document_id']} | {group} | {title} | {entry['pdf_pages']} | "
            f"[{entry['document_id']}](<{entry['path']}>) |"
        )
    (destination / "backup_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        **{
            key: manifest[key]
            for key in ("pdf_count", "source_location_count", "selected_count", "unselected_count")
        },
        "new_files": copied,
        "groups": dict(
            Counter("selected" if e["selected"] else "unselected" for e in entries.values())
        ),
    }
