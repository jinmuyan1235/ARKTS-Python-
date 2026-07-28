"""Immutable filesystem snapshots for visually reviewed detector datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_FILES = (
    "detector_training_manifest.csv",
    "visual_verified.csv",
    "visual_rejected.csv",
    "missing_files.csv",
    "review_consistency_report.json",
)
SNAPSHOT_DIRECTORIES = ("single_reviews", "analysis")
SNAPSHOT_METADATA_FILES = ("holdout_papers.json", "holdout_protocol.json")


def _csv_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_visual_dataset(
    *,
    version: str,
    review_dir: str | Path,
    output: str | Path,
    dataset_role: str = "development",
) -> dict[str, Any]:
    """Copy all first-round review artifacts into a new non-overwriting version."""
    if not version.strip():
        raise ValueError("version must not be empty.")
    if dataset_role not in {"development", "holdout"}:
        raise ValueError(f"Unsupported dataset role: {dataset_role}")
    review_root = Path(review_dir).expanduser().resolve()
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"Dataset version already exists and will not be overwritten: {target}")
    required = [review_root / name for name in (*SNAPSHOT_FILES, *SNAPSHOT_DIRECTORIES)]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Required visual-review artifacts are missing: " + ", ".join(missing))
    paper_metadata_path = review_root / "holdout_papers.json"
    if dataset_role == "holdout" and not paper_metadata_path.is_file():
        raise FileNotFoundError(f"Holdout paper metadata is required: {paper_metadata_path}")
    with (review_root / "machine_review_manifest.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        machine_rows = list(csv.DictReader(handle))
    reviewed_ids = {path.stem for path in (review_root / "single_reviews").glob("*.json")}
    remaining = [
        str(row.get("sample_id") or "") for row in machine_rows
        if (dataset_role == "holdout" or row.get("verification_status") == "pending_human_review")
        and str(row.get("sample_id") or "") not in reviewed_ids
    ]
    if remaining:
        raise ValueError(
            "Visual remaining must be zero before snapshot; "
            f"found {len(remaining)} unreviewed machine-routed or machine-rejected samples."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for name in SNAPSHOT_FILES:
            shutil.copy2(review_root / name, staging / name)
        for name in SNAPSHOT_DIRECTORIES:
            shutil.copytree(review_root / name, staging / name)
        for name in SNAPSHOT_METADATA_FILES:
            source = review_root / name
            if source.is_file():
                shutil.copy2(source, staging / name)
        with (staging / "detector_training_manifest.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            detector_rows = list(csv.DictReader(handle))
        class_counts: dict[str, int] = {}
        documents: set[str] = set()
        for row in detector_rows:
            status = str(row.get("visual_review_status") or "unknown")
            class_counts[status] = class_counts.get(status, 0) + 1
            document = str(row.get("source_document") or "").strip()
            if document:
                documents.add(document)
        paper_metadata: list[dict[str, Any]] = []
        if (staging / "holdout_papers.json").is_file():
            payload = json.loads((staging / "holdout_papers.json").read_text(encoding="utf-8"))
            paper_metadata = list(payload.get("papers") or payload)
        if dataset_role == "holdout" and len(paper_metadata) != 3:
            raise ValueError("A holdout snapshot must describe exactly three papers.")
        summary = {
            "version": version,
            "dataset_role": dataset_role,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_review_dir": str(review_root),
            "detector_training_samples": _csv_count(staging / "detector_training_manifest.csv"),
            "visual_verified_samples": _csv_count(staging / "visual_verified.csv"),
            "visual_rejected_samples": _csv_count(staging / "visual_rejected.csv"),
            "missing_file_samples": _csv_count(staging / "missing_files.csv"),
            "single_review_records": len(list((staging / "single_reviews").glob("*.json"))),
            "analysis_files": len([path for path in (staging / "analysis").rglob("*") if path.is_file()]),
            "source_documents": sorted(documents),
            "visual_class_counts": dict(sorted(class_counts.items())),
            "papers": paper_metadata,
        }
        (staging / "dataset_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        frozen_files = sorted(
            path for path in staging.rglob("*")
            if path.is_file() and path.name != "checksums.sha256"
        )
        checksum_lines = [f"{_sha256(path)}  {path.relative_to(staging).as_posix()}" for path in frozen_files]
        (staging / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**summary, "output": str(target), "checksummed_files": len(checksum_lines)}
