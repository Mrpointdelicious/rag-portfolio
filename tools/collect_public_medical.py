"""Bounded publisher/PMC Cloud acquisition; source bytes always stay Git-ignored."""

import argparse
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

S3 = "https://pmc-oa-opendata.s3.amazonaws.com/"
ALLOWED = {"pmc-oa-opendata.s3.amazonaws.com", "journals.plos.org", "storage.googleapis.com"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Collector:
    def __init__(self, root, *, max_requests=60):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.max_requests = max_requests
        self.client = httpx.Client(timeout=30, follow_redirects=False)

    def get(self, url, name, maximum=20_000_000):
        if urlsplit(url).hostname not in ALLOWED or len(self.calls) >= self.max_requests:
            raise ValueError("Unapproved origin or acquisition request limit")
        row = {"url": url, "at": datetime.now(UTC).isoformat(), "asset": name}
        self.calls.append(row)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data = path.read_bytes()
            row.update(status="local_reuse", sha256=sha(data), byte_count=len(data))
            return data
        try:
            with self.client.stream("GET", url) as response:
                row.update(http_status=response.status_code)
                if response.status_code == 302 and urlsplit(url).hostname == "journals.plos.org":
                    location = response.headers.get("location", "")
                    target = urlsplit(location)
                    if (
                        target.scheme != "https"
                        or target.hostname != "storage.googleapis.com"
                        or not target.path.startswith("/plos-corpus-prod/")
                    ):
                        raise ValueError("Publisher redirect outside approved storage")
                    row["redirect"] = urlunsplit(
                        (target.scheme, target.netloc, target.path, "", "")
                    )
                    # The publisher's short-lived public asset URL is used only in memory.
                    # Do not retain its signed query parameters in logs or manifests.
                    with self.client.stream("GET", location) as redirected:
                        redirected.raise_for_status()
                        raw = bytearray()
                        for part in redirected.iter_bytes():
                            raw.extend(part)
                            if len(raw) > maximum:
                                raise ValueError("Asset size limit exceeded")
                    data = bytes(raw)
                    path.write_bytes(data)
                    row.update(
                        status="downloaded",
                        sha256=sha(data),
                        byte_count=len(data),
                        redirected_http_status=200,
                    )
                    return data
                response.raise_for_status()
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > maximum:
                        raise ValueError("Asset size limit exceeded")
            data = bytes(raw)
            path.write_bytes(data)
            row.update(status="downloaded", sha256=sha(data), byte_count=len(data))
            return data
        except (httpx.HTTPError, ValueError) as exc:
            row.update(status="not_downloaded", error=type(exc).__name__)
            raise ValueError(f"Acquisition failed for {name}") from None

    def pmc(self, candidate):
        cid, pmcid = candidate["candidate_id"], candidate["pmcid"]
        listing = self.get(
            S3 + "?" + urlencode({"list-type": 2, "prefix": pmcid + ".", "max-keys": 100}),
            cid + "/cloud-list.xml",
            500_000,
        )
        tree = ET.fromstring(listing)
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [r.text for r in tree.findall("s:Contents/s:Key", ns)]
        versions = sorted({k.split("/")[0] for k in keys if k.endswith(".json")})
        valid = []
        for prefix in versions[:3]:
            key = next(k for k in keys if k == prefix + "/" + prefix + ".json")
            raw = self.get(S3 + quote(key), cid + "/" + prefix + ".json", 500_000)
            meta = json.loads(raw)
            if str(meta.get("is_manuscript", "")).lower() in {"no", "false", "0"}:
                valid.append((prefix, meta))
        if len(valid) != 1:
            raise ValueError("No unambiguous published PMC version; manual selection required")
        prefix, meta = valid[0]
        assets = []
        for extension in ("xml", "pdf"):
            key = prefix + "/" + prefix + "." + extension
            if key not in keys:
                assets.append({"format": extension, "status": "not_downloaded", "reason": "absent"})
                continue
            data = self.get(S3 + quote(key), cid + "/article." + extension)
            assets.append(self.describe(candidate, extension, data, S3 + quote(key)))
        return {"cloud_version": prefix, "cloud_metadata": meta, "assets": assets}

    def plos(self, candidate):
        assets = []
        for fmt, kind in (("xml", "manuscript"), ("pdf", "printable")):
            url = "https://journals.plos.org/plosone/article/file?" + urlencode(
                {"id": candidate["doi"], "type": kind}
            )
            try:
                raw = self.get(url, candidate["candidate_id"] + "/article." + fmt)
                assets.append(self.describe(candidate, fmt, raw, url))
            except ValueError as exc:
                assets.append({"format": fmt, "status": "not_downloaded", "reason": str(exc)})
            time.sleep(0.4)
        return {"assets": assets}

    @staticmethod
    def describe(candidate, fmt, data, url):
        result = {
            "format": fmt,
            "status": "downloaded",
            "url": url,
            "byte_count": len(data),
            "sha256": sha(data),
        }
        if fmt == "xml":
            root = ET.fromstring(data)
            if root.tag != "article":
                raise ValueError("Expected JATS article, not an error or HTML page")
            ids = {
                e.get("pub-id-type"): "".join(e.itertext()).strip()
                for e in root.findall("./front/article-meta/article-id")
            }
            if ids.get("doi", "").lower() != candidate["doi"].lower():
                raise ValueError("Downloaded DOI mismatch")
            title = "".join(root.find("./front/article-meta/title-group/article-title").itertext())
            license_node = root.find("./front/article-meta/permissions/license")
            result.update(
                doi=ids["doi"],
                title=title,
                article_type=root.get("article-type"),
                pmcid=ids.get("pmc", ids.get("pmcid")),
                license_xml=ET.tostring(license_node, encoding="unicode")
                if license_node is not None
                else None,
            )
            result["identity_verified"] = True
        elif not data.startswith(b"%PDF-"):
            raise ValueError("Expected PDF bytes")
        return result


def main(args):
    manifest = json.loads(args.candidates.read_text(encoding="utf8"))
    collector = Collector(args.output)
    result = {
        "execution": "real publisher downloads / official PMC Cloud",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "articles": [],
    }
    try:
        candidates = list(manifest["additional_six"])
        if args.include_d161:
            candidates.append(
                {"candidate_id": "D161", "pmcid": "PMC9856764", "doi": "10.3390/brainsci13010092"}
            )
        for candidate in candidates:
            row = {"candidate_id": candidate["candidate_id"], "expected_doi": candidate["doi"]}
            try:
                row.update(
                    collector.pmc(candidate) if candidate["pmcid"] else collector.plos(candidate)
                )
            except (ValueError, StopIteration, ET.ParseError) as exc:
                row.update(status="BLOCKED", reason=str(exc))
            result["articles"].append(row)
            (args.output / "acquisition.json").write_text(
                json.dumps({**result, "requests": collector.calls}, ensure_ascii=False, indent=2),
                encoding="utf8",
            )
            print(
                json.dumps(
                    {
                        "candidate": row["candidate_id"],
                        "status": row.get("status", "collected"),
                        "assets": [(a["format"], a["status"]) for a in row.get("assets", [])],
                    }
                ),
                flush=True,
            )
    finally:
        collector.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates", type=Path, default=Path("eval/public_medical_corpus_v1/candidates.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".local/public_medical_corpus_v1/assets")
    )
    parser.add_argument("--include-d161", action="store_true")
    main(parser.parse_args())
