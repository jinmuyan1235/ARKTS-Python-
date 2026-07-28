"""Freeze a completed page-level annotation workspace without overwriting versions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source: Path, output: Path, *, version: str) -> dict:
    source = source.resolve(); output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite frozen page dataset: {output}")
    annotations = json.loads((source / "annotations.json").read_text(encoding="utf-8"))
    pages = annotations.get("pages", {})
    pending = [page_id for page_id, page in pages.items() if page.get("annotation_status") != "completed"]
    if pending:
        raise ValueError(f"All pages must be completed before freezing; remaining={len(pending)}")
    output.mkdir(parents=True)
    shutil.copytree(source / "pages", output / "pages")
    for name in ("annotations.json", "protocol.json"):
        shutil.copy2(source / name, output / name)
    manifest_fields = [
        "page_id", "source_document", "pmcid", "page_number", "image_path",
        "width", "height", "annotation_status", "layout_tags", "annotation_count",
    ]
    with (output / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_fields)
        writer.writeheader()
        for page_id, page in sorted(pages.items()):
            writer.writerow({
                **{field: page.get(field, "") for field in manifest_fields},
                "page_id": page_id, "layout_tags": ",".join(page.get("layout_tags", [])),
                "annotation_count": len(page.get("annotations", [])),
            })
    classes = Counter(
        item["class"] for page in pages.values() for item in page.get("annotations", [])
    )
    documents = Counter(page["source_document"] for page in pages.values())
    protocol = json.loads((source / "protocol.json").read_text(encoding="utf-8"))
    summary = {
        "version": version, "dataset_role": "page_holdout", "page_count": len(pages),
        "annotation_count": sum(classes.values()), "class_counts": dict(sorted(classes.items())),
        "documents": dict(sorted(documents.items())), "immutable": True,
        "protocol_git_sha": protocol.get("git_sha"),
        "config_sha256": protocol.get("config_sha256"),
    }
    (output / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256")
    lines = [f"{_sha256(path)}  {path.relative_to(output).as_posix()}" for path in files]
    (output / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/page_annotations/visual-page-holdout-v0.1")
    parser.add_argument("--version", default="visual-page-holdout-v0.1")
    parser.add_argument("--output", default="data/datasets/visual-page-holdout-v0.1")
    args = parser.parse_args()
    summary = snapshot(Path(args.source), Path(args.output), version=args.version)
    print(json.dumps(summary, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
