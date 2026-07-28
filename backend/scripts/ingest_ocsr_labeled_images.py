"""Ingest manually labeled OCSR images into the benchmark manifest format."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import shutil
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.dataset import ManifestValidationError, load_manifest
from src.utils.file_utils import ensure_directory, safe_stem


MANIFEST_FIELDS = [
    "sample_id",
    "image_path",
    "ground_truth_smiles",
    "expected_action",
    "category",
    "source",
    "split",
    "scaffold_key",
    "source_document",
    "image_quality",
    "complexity",
    "perturbation",
    "structure_features",
    "notes",
]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def _resolve_image(raw: str, image_root: Path) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else image_root / path).resolve()


def _copy_image(source: Path, image_dir: Path, sample_id: str) -> Path:
    suffix = source.suffix.lower() or ".png"
    target = image_dir / f"{safe_stem(sample_id)}{suffix}"
    counter = 2
    while target.exists():
        target = image_dir / f"{safe_stem(sample_id)}_{counter}{suffix}"
        counter += 1
    shutil.copy2(source, target)
    return target


def _manifest_row(row: dict[str, str], copied_image: Path, output_root: Path) -> dict[str, Any]:
    sample_id = row.get("sample_id") or copied_image.stem
    expected_action = (row.get("expected_action") or "recognize").lower()
    return {
        "sample_id": sample_id,
        "image_path": copied_image.resolve().relative_to(output_root.resolve()).as_posix(),
        "ground_truth_smiles": row.get("ground_truth_smiles") or row.get("smiles") or "",
        "expected_action": expected_action,
        "category": row.get("category") or ("manual_reject" if expected_action == "reject" else "manual_labeled"),
        "source": row.get("source") or "manual_collection",
        "split": row.get("split") or "test",
        "scaffold_key": row.get("scaffold_key") or "unspecified",
        "source_document": row.get("source_document") or row.get("source_url") or "unspecified",
        "image_quality": row.get("image_quality") or "unspecified",
        "complexity": row.get("complexity") or "unspecified",
        "perturbation": row.get("perturbation") or "none",
        "structure_features": row.get("structure_features") or "unspecified",
        "notes": row.get("notes") or "",
    }


def ingest(labels_csv: Path, image_root: Path, output_root: Path) -> Path:
    output_root = ensure_directory(output_root)
    image_dir = ensure_directory(output_root / "images")
    rows: list[dict[str, Any]] = []
    for row in _read_rows(labels_csv):
        sample_id = row.get("sample_id") or Path(row.get("image_path") or "").stem
        if not sample_id:
            raise ValueError(f"Row is missing sample_id and image_path: {row}")
        source_image = _resolve_image(row.get("image_path") or "", image_root)
        if not source_image.is_file():
            raise FileNotFoundError(f"Image does not exist for sample_id={sample_id}: {source_image}")
        copied = _copy_image(source_image, image_dir, sample_id)
        rows.append(_manifest_row({**row, "sample_id": sample_id}, copied, output_root))
    manifest = output_root / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    try:
        load_manifest(manifest, output_root)
    except ManifestValidationError as exc:
        raise SystemExit(f"Manifest validation failed after ingest:\n{exc}") from exc
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, help="CSV with image_path, sample_id and labels.")
    parser.add_argument("--image-root", default=".", help="Root for relative image_path values in --labels.")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "data" / "ocsr_manual_labeled"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = ingest(
        labels_csv=Path(args.labels).expanduser().resolve(),
        image_root=Path(args.image_root).expanduser().resolve(),
        output_root=Path(args.output_root).expanduser().resolve(),
    )
    print(f"Wrote validated manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
