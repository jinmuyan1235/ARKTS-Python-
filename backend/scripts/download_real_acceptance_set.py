"""Download and deterministically materialize the real OCSR starter acceptance set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "data" / "ocsr_real_acceptance"
SOURCE_MANIFEST = DATASET_ROOT / "source_manifest.csv"
DOWNLOAD_METADATA = DATASET_ROOT / "download_metadata.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"source manifest does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"source manifest is empty: {path}")
    required = {
        "sample_id",
        "image_path",
        "expected_sha256",
        "source_key",
        "source_url",
        "source_sha256",
        "source_project",
        "source_version",
        "source_license",
        "operation",
        "operation_args",
    }
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"source manifest missing required fields: {', '.join(missing)}")
    return rows


def resolve_dataset_path(relative_path: str) -> Path:
    path = (DATASET_ROOT / relative_path).resolve()
    try:
        path.relative_to(DATASET_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"image_path escapes dataset root: {relative_path}") from exc
    return path


def download_to_temp(url: str, temp_dir: Path) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="real_acceptance_", suffix=".download", dir=temp_dir, delete=False)
    temp_path = Path(handle.name)
    handle.close()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "molecule-vision-real-acceptance-downloader"})
        with urllib.request.urlopen(request, timeout=60) as response, temp_path.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def load_source_images(rows: list[dict[str, str]], temp_dir: Path) -> dict[str, Image.Image]:
    source_rows: dict[str, dict[str, str]] = {}
    for row in rows:
        source_rows.setdefault(row["source_key"], row)
    images: dict[str, Image.Image] = {}
    for source_key, row in source_rows.items():
        temp_path = download_to_temp(row["source_url"], temp_dir)
        actual_sha = sha256_file(temp_path)
        expected_sha = row["source_sha256"].strip().lower()
        if actual_sha.lower() != expected_sha:
            temp_path.unlink(missing_ok=True)
            raise ValueError(
                f"source SHA-256 mismatch for {source_key}: expected {expected_sha}, got {actual_sha}"
            )
        with Image.open(temp_path) as image:
            images[source_key] = image.convert("RGB")
        temp_path.unlink(missing_ok=True)
    return images


def materialize_image(source: Image.Image, operation: str, operation_args: dict[str, Any]) -> Image.Image:
    image = source.copy()
    crop_box = operation_args.get("crop_box")
    if crop_box:
        image = image.crop(tuple(int(value) for value in crop_box))
    if operation == "full":
        return image
    if operation == "crop":
        return image
    if operation == "lowres":
        lowres_size = tuple(int(value) for value in operation_args["lowres_size"])
        return image.resize(lowres_size, Image.Resampling.BILINEAR).resize(image.size, Image.Resampling.BILINEAR)
    if operation == "rotate":
        return image.rotate(
            float(operation_args["degrees"]),
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        )
    if operation == "binary":
        threshold = int(operation_args.get("threshold", 128))
        median_size = int(operation_args.get("median_size", 3))
        binary = image.convert("L").point(lambda pixel: 255 if pixel > threshold else 0, mode="1").convert("RGB")
        return binary.filter(ImageFilter.MedianFilter(median_size))
    if operation == "jpeg":
        return image
    raise ValueError(f"unsupported operation: {operation}")


def save_materialized_image(image: Image.Image, destination: Path, operation: str, operation_args: dict[str, Any]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    suffix = destination.suffix.lower()
    if operation == "jpeg" or suffix in {".jpg", ".jpeg"}:
        image.save(
            temp,
            format="JPEG",
            quality=int(operation_args["quality"]),
            optimize=bool(operation_args.get("optimize", True)),
            subsampling=int(operation_args.get("subsampling", 2)),
        )
    else:
        image.save(temp, format="PNG", optimize=bool(operation_args.get("optimize", True)))
    return temp


def materialize_rows(rows: list[dict[str, str]], source_images: dict[str, Image.Image]) -> tuple[int, list[dict[str, Any]]]:
    materialized = 0
    records: list[dict[str, Any]] = []
    for row in rows:
        destination = resolve_dataset_path(row["image_path"])
        expected_sha = row["expected_sha256"].strip().lower()
        operation_args = json.loads(row["operation_args"] or "{}")
        image = materialize_image(source_images[row["source_key"]], row["operation"], operation_args)
        temp = save_materialized_image(image, destination, row["operation"], operation_args)
        actual_sha = sha256_file(temp)
        if actual_sha.lower() != expected_sha:
            temp.unlink(missing_ok=True)
            raise ValueError(
                f"generated SHA-256 mismatch for {row['image_path']}: expected {expected_sha}, got {actual_sha}"
            )
        os.replace(temp, destination)
        materialized += 1
        records.append({
            "sample_id": row["sample_id"],
            "image_path": row["image_path"],
            "sha256": actual_sha,
            "source_project": row["source_project"],
            "source_version": row["source_version"],
            "source_url": row["source_url"],
            "source_license": row["source_license"],
            "operation": row["operation"],
            "status": "materialized",
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return materialized, records


def _metadata_record(row: dict[str, str], sha256: str, status: str) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "image_path": row["image_path"],
        "sha256": sha256,
        "source_project": row["source_project"],
        "source_version": row["source_version"],
        "source_url": row["source_url"],
        "source_license": row["source_license"],
        "operation": row["operation"],
        "status": status,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def rows_needing_materialization(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int, list[dict[str, Any]]]:
    needed: list[dict[str, str]] = []
    skipped = 0
    skipped_records: list[dict[str, Any]] = []
    for row in rows:
        destination = resolve_dataset_path(row["image_path"])
        expected_sha = row["expected_sha256"].strip().lower()
        if destination.exists():
            actual_sha = sha256_file(destination)
            if actual_sha.lower() == expected_sha:
                skipped += 1
                skipped_records.append(_metadata_record(row, actual_sha, "skipped"))
                continue
            raise ValueError(
                f"existing file SHA-256 mismatch for {row['image_path']}: expected {expected_sha}, got {actual_sha}. "
                "Remove the file and rerun after confirming the source manifest."
            )
        needed.append(row)
    return needed, skipped, skipped_records


def write_download_metadata(records: list[dict[str, Any]], skipped: int, materialized: int) -> None:
    payload = {
        "dataset_root": str(DATASET_ROOT.resolve()),
        "source_manifest": str(SOURCE_MANIFEST.resolve()),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "materialized_count": materialized,
        "skipped_count": skipped,
        "records": records,
    }
    DOWNLOAD_METADATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def download_acceptance_set(source_manifest: Path = SOURCE_MANIFEST) -> dict[str, int]:
    rows = read_source_manifest(source_manifest)
    needed, skipped, skipped_records = rows_needing_materialization(rows)
    if not needed:
        write_download_metadata(skipped_records, skipped, 0)
        return {"downloaded": 0, "skipped": skipped, "failed": 0}
    temp_dir = DATASET_ROOT / ".download_tmp"
    source_images = load_source_images(needed, temp_dir)
    materialized, records = materialize_rows(needed, source_images)
    write_download_metadata([*skipped_records, *records], skipped, materialized)
    return {"downloaded": materialized, "skipped": skipped, "failed": 0}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", default=str(SOURCE_MANIFEST))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        counts = download_acceptance_set(Path(args.source_manifest).expanduser().resolve())
    except Exception as exc:
        print(json.dumps({"downloaded": 0, "skipped": 0, "failed": 1, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
