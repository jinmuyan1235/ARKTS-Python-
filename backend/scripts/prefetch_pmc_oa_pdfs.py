"""Prefetch official PMC OA PDFs with curl into the collection HTTP cache."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET


PMC_AWS_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"


def _curl_bytes(url: str) -> bytes:
    return subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "180", url],
        check=True, capture_output=True,
    ).stdout


def _latest_version(pmcid: str) -> int:
    payload = _curl_bytes(f"{PMC_AWS_BUCKET}/?list-type=2&prefix={pmcid}.&delimiter=/")
    root = ET.fromstring(payload)
    pattern = re.compile(rf"{re.escape(pmcid)}\.(\d+)/")
    versions = [
        int(match.group(1))
        for node in root.findall(".//{*}CommonPrefixes/{*}Prefix")
        if (match := pattern.fullmatch((node.text or "").strip()))
    ]
    if not versions:
        raise ValueError(f"No official PMC OA PDF version found for {pmcid}")
    return max(versions)


def _prefetch(cache_dir: Path, pmcid: str) -> dict[str, str | int]:
    version = _latest_version(pmcid)
    url = f"{PMC_AWS_BUCKET}/{pmcid}.{version}/{pmcid}.{version}.pdf"
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    body_path = cache_dir / f"{key}.bin"
    metadata_path = cache_dir / f"{key}.json"
    if body_path.is_file() and metadata_path.is_file():
        return {"pmcid": pmcid, "url": url, "status": "cached", "bytes": body_path.stat().st_size}
    with tempfile.NamedTemporaryFile(prefix=f"{pmcid}-", suffix=".pdf", dir=cache_dir, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run(
            [
                "curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "600",
                "--output", str(temporary), url,
            ],
            check=True,
        )
        payload = temporary.read_bytes()
        if not payload.startswith(b"%PDF"):
            raise ValueError(f"Official object is not a PDF: {url}")
        digest = hashlib.sha256(payload).hexdigest()
        temporary.replace(body_path)
        metadata_path.write_text(json.dumps({
            "url": url, "status_code": 200, "sha256": digest,
            "headers": {"transport": "curl", "source": "PMC OA Open Data"}, "attempt": 1,
        }, indent=2), encoding="utf-8")
        return {"pmcid": pmcid, "url": url, "status": "downloaded", "bytes": len(payload), "sha256": digest}
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--pmcid", action="append", required=True)
    args = parser.parse_args()
    cache_dir = Path(args.dataset_root).resolve() / "http_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results = [_prefetch(cache_dir, str(value).strip().upper()) for value in args.pmcid]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
