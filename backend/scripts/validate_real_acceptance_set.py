"""Validate the deterministic real OCSR starter acceptance set."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.dataset import ManifestValidationError, load_manifest

DATASET_ROOT = PROJECT_ROOT / "data" / "ocsr_real_acceptance"
MANIFEST = DATASET_ROOT / "manifest.csv"
SOURCE_MANIFEST = DATASET_ROOT / "source_manifest.csv"
CHECKSUMS = DATASET_ROOT / "checksums.sha256"
DOWNLOADER = PROJECT_ROOT / "scripts" / "download_real_acceptance_set.py"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"required file does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_checksums(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"required checksum file does not exist: {path}")
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"{path}:{line_number}: expected '<sha256>  <relative_path>'.")
        checksums[parts[1].strip()] = parts[0].strip().lstrip("\ufeff").lower()
    return checksums


def _validate_downloader_exists() -> None:
    if not DOWNLOADER.is_file():
        raise FileNotFoundError(f"deterministic downloader is missing: {DOWNLOADER}")


def _validate_source_manifest(manifest_rows: list[dict[str, str]], source_rows: list[dict[str, str]]) -> None:
    errors: list[str] = []
    source_by_sample = {row.get("sample_id", ""): row for row in source_rows}
    manifest_sample_ids = {row.get("sample_id", "") for row in manifest_rows}
    if "" in source_by_sample or "" in manifest_sample_ids:
        errors.append("sample_id must be non-empty in both manifest.csv and source_manifest.csv.")
    missing_source = sorted(manifest_sample_ids - set(source_by_sample))
    extra_source = sorted(set(source_by_sample) - manifest_sample_ids)
    if missing_source:
        errors.append(f"source_manifest.csv missing sample_id rows: {', '.join(missing_source)}")
    if extra_source:
        errors.append(f"source_manifest.csv contains rows not present in manifest.csv: {', '.join(extra_source)}")
    for row in manifest_rows:
        sample_id = row.get("sample_id", "")
        source = source_by_sample.get(sample_id)
        if not source:
            continue
        if source.get("image_path") != row.get("image_path"):
            errors.append(f"{sample_id}: image_path differs between manifest and source manifest.")
        if (source.get("expected_sha256") or "").lower() != (row.get("image_sha256") or "").lower():
            errors.append(f"{sample_id}: expected_sha256 differs from manifest image_sha256.")
        for field in ("source_url", "source_sha256", "source_version", "source_license", "operation"):
            if not (source.get(field) or "").strip():
                errors.append(f"{sample_id}: source_manifest.csv field '{field}' is empty.")
    if errors:
        raise ValueError("\n".join(errors))


def _validate_checksums(manifest_rows: list[dict[str, str]], checksums: dict[str, str]) -> None:
    errors: list[str] = []
    manifest_by_path = {row.get("image_path", ""): (row.get("image_sha256") or "").lower() for row in manifest_rows}
    missing = sorted(set(manifest_by_path) - set(checksums))
    extra = sorted(set(checksums) - set(manifest_by_path))
    if missing:
        errors.append(f"checksums.sha256 missing paths: {', '.join(missing)}")
    if extra:
        errors.append(f"checksums.sha256 contains paths not in manifest.csv: {', '.join(extra)}")
    for path, expected in manifest_by_path.items():
        if checksums.get(path) and checksums[path] != expected:
            errors.append(f"{path}: checksum differs between manifest.csv and checksums.sha256.")
    if errors:
        raise ValueError("\n".join(errors))


def validate_real_acceptance_set(
    manifest: Path = MANIFEST,
    dataset_root: Path = DATASET_ROOT,
    source_manifest: Path = SOURCE_MANIFEST,
    checksums_path: Path = CHECKSUMS,
) -> dict[str, Any]:
    _validate_downloader_exists()
    manifest_rows = _read_csv(manifest)
    source_rows = _read_csv(source_manifest)
    _validate_source_manifest(manifest_rows, source_rows)
    _validate_checksums(manifest_rows, _read_checksums(checksums_path))
    samples = load_manifest(manifest, dataset_root, require_real_metadata=True)
    positive_count = sum(sample.expected_action != "reject" for sample in samples)
    negative_count = sum(sample.expected_action == "reject" for sample in samples)
    return {
        "passed": True,
        "manifest": str(manifest.resolve()),
        "dataset_root": str(dataset_root.resolve()),
        "sample_count": len(samples),
        "positive_sample_count": positive_count,
        "negative_sample_count": negative_count,
        "source_manifest_rows": len(source_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--dataset-root", default=str(DATASET_ROOT))
    parser.add_argument("--source-manifest", default=str(SOURCE_MANIFEST))
    parser.add_argument("--checksums", default=str(CHECKSUMS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate_real_acceptance_set(
            manifest=Path(args.manifest).expanduser().resolve(),
            dataset_root=Path(args.dataset_root).expanduser().resolve(),
            source_manifest=Path(args.source_manifest).expanduser().resolve(),
            checksums_path=Path(args.checksums).expanduser().resolve(),
        )
    except ManifestValidationError as exc:
        print(json.dumps({"passed": False, "errors": exc.errors}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"passed": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
