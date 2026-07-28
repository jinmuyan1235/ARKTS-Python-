"""Prepare a deterministic, detector-blind 10-pages-per-document annotation workspace."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.documents.candidate_screening import get_crop_screening_config, get_proposal_config


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()


def _stratified_pages(page_count: int, count: int, rng: random.Random) -> list[int]:
    """Choose one page from each position stratum without reading detector output."""
    if page_count < count:
        raise ValueError(f"Document has {page_count} pages; at least {count} are required.")
    selected: list[int] = []
    for index in range(count):
        start = index * page_count // count
        stop = (index + 1) * page_count // count
        selected.append(rng.randrange(start, max(start + 1, stop)) + 1)
    return selected


def prepare(collection_root: Path, output: Path, *, seed: int, pages_per_document: int, dpi: int) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite page annotation workspace: {output}")
    pdfs = sorted((collection_root / "sources" / "pmc").glob("PMC*/PMC*.pdf"))
    if len(pdfs) != 3:
        raise ValueError(f"Expected exactly three holdout PDFs, found {len(pdfs)}.")
    forbidden = {"PMC6225359", "PMC9822073"}
    if any(pdf.stem.upper() in forbidden for pdf in pdfs):
        raise ValueError("Development document found in page holdout sources.")
    import fitz

    pages_dir = output / "pages"
    pages_dir.mkdir(parents=True)
    rng = random.Random(seed)
    rows: list[dict] = []
    annotation_pages: dict[str, dict] = {}
    selections: list[dict] = []
    for pdf_path in pdfs:
        pmcid = pdf_path.stem.upper()
        document = fitz.open(pdf_path)
        numbers = _stratified_pages(document.page_count, pages_per_document, rng)
        selections.append({"pmcid": pmcid, "page_count": document.page_count, "selected_pages": numbers})
        for page_number in numbers:
            page_id = f"{pmcid}_p{page_number:03d}"
            relative_image = Path("pages") / f"{page_id}.png"
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            pixmap.save(output / relative_image)
            row = {
                "page_id": page_id, "source_document": pmcid, "pmcid": pmcid,
                "page_number": page_number, "image_path": relative_image.as_posix(),
                "width": pixmap.width, "height": pixmap.height,
                "annotation_status": "pending", "layout_tags": "",
            }
            rows.append(row)
            annotation_pages[page_id] = {
                **row, "layout_tags": [], "annotations": [], "annotator": "", "updated_at": "",
            }
        document.close()
    configs = {
        "proposal": {name: asdict(get_proposal_config(name)) for name in ("baseline", "candidate")},
        "crop_screening": {name: asdict(get_crop_screening_config(name)) for name in ("baseline", "candidate")},
    }
    protocol = {
        "schema_version": 1, "dataset_role": "page_holdout", "seed": seed,
        "pages_per_document": pages_per_document, "render_dpi": dpi,
        "selection_rule": "one seeded random page from each of ten equal page-position strata; detector outputs and performance were not inspected",
        "selection": selections, "git_sha": _git_sha(), "configs": configs,
        "config_sha256": _canonical_sha(configs),
    }
    (output / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "annotations.json").write_text(
        json.dumps({"schema_version": 1, "pages": annotation_pages}, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    with (output / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return {"output": str(output), "page_count": len(rows), "selection": selections, "config_sha256": protocol["config_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-root", default="data/ocsr_holdout_collection")
    parser.add_argument("--output", default="data/page_annotations/visual-page-holdout-v0.1")
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--pages-per-document", type=int, default=10)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    result = prepare(Path(args.collection_root).resolve(), Path(args.output).resolve(), seed=args.seed, pages_per_document=args.pages_per_document, dpi=args.dpi)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
