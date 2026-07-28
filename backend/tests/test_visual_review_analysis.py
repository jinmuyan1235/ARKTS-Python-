"""Tests for first-round visual-review analysis and immutable snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from src.datasets.visual_dataset_snapshot import snapshot_visual_dataset
from src.datasets.visual_review_analysis import analyze_visual_review


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["sample_id"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _audit(review: Path, row: dict[str, str], status: str, bbox_after: list[int]) -> None:
    payload = {
        "sample_id": row["sample_id"], "visual_review_status": status,
        "bbox_before": [10, 10, 90, 90], "bbox_after": bbox_after,
        "region_type_after": "molecule" if status == "valid_single_molecule_crop" else status,
        "source_queue_row": row,
    }
    (review / "single_reviews" / f"{row['sample_id']}.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _review_fixture(tmp_path: Path) -> Path:
    review = tmp_path / "review"
    (review / "single_reviews").mkdir(parents=True)
    rows = [
        {
            "sample_id": "molecule-valid", "verification_status": "pending_human_review",
            "machine_category": "molecule", "source_document": "doc-a", "source_page_path": "page-1.png",
            "risk_reasons": "[]", "deterministic_errors": "[]", "ensemble_smiles": "CCO",
        },
        {
            "sample_id": "molecule-text", "verification_status": "pending_human_review",
            "machine_category": "molecule", "source_document": "doc-a", "source_page_path": "page-1.png",
            "risk_reasons": "[]", "deterministic_errors": "[]", "ensemble_smiles": "",
        },
        {
            "sample_id": "negative-smiles", "verification_status": "pending_human_review",
            "machine_category": "text", "source_document": "doc-b", "source_page_path": "page-2.png",
            "risk_reasons": '["negative_sample_has_valid_smiles"]', "deterministic_errors": "[]",
            "molscribe_smiles": "CCO",
        },
        {
            "sample_id": "machine-rejected", "verification_status": "rejected_invalid",
            "machine_category": "invalid_crop", "source_document": "doc-b", "source_page_path": "page-2.png",
            "risk_reasons": "[]", "deterministic_errors": '["image_decode_failed"]', "ensemble_smiles": "",
        },
    ]
    _write_csv(review / "machine_review_manifest.csv", rows)
    _audit(review, rows[0], "valid_single_molecule_crop", [10, 10, 90, 90])
    _audit(review, rows[1], "text", [12, 10, 90, 90])
    _audit(review, rows[2], "text", [10, 10, 90, 90])
    _write_csv(review / "detector_training_manifest.csv", [
        {"sample_id": "molecule-valid", "expected_action": "recognize"},
        {"sample_id": "molecule-text", "expected_action": "reject"},
        {"sample_id": "negative-smiles", "expected_action": "reject"},
    ])
    return review


def test_visual_review_analysis_writes_confusion_and_required_metrics(tmp_path: Path) -> None:
    review = _review_fixture(tmp_path)

    result = analyze_visual_review(review)

    assert result["machine_molecule_visual_validity_rate"] == 0.5
    assert result["negative_candidates_with_valid_smiles"] == 1
    confusion = _read_csv(review / "analysis" / "machine_vs_human_confusion.csv")
    molecule = next(row for row in confusion if row["machine_category"] == "molecule")
    assert molecule["valid_single_molecule_crop"] == "1"
    assert molecule["text"] == "1"
    reasons = _read_csv(review / "analysis" / "machine_rejection_reasons.csv")
    assert {row["rejection_reason"] for row in reasons} == {"image_decode_failed"}
    bbox = json.loads((review / "analysis" / "bbox_correction_summary.json").read_text(encoding="utf-8"))
    assert bbox["bbox_modified"] == 1
    report = (review / "analysis" / "visual_review_report.md").read_text(encoding="utf-8")
    assert "cannot estimate complete-page detection recall" in report
    assert (review / "analysis" / "per_document_metrics.csv").is_file()
    assert (review / "analysis" / "visual_class_counts.csv").is_file()


def test_visual_dataset_snapshot_copies_checksums_and_refuses_overwrite(tmp_path: Path) -> None:
    review = _review_fixture(tmp_path)
    analyze_visual_review(review)
    _write_csv(review / "visual_verified.csv", [{"sample_id": "molecule-valid"}])
    _write_csv(review / "visual_rejected.csv", [{"sample_id": "molecule-text"}])
    _write_csv(review / "missing_files.csv", [])
    (review / "review_consistency_report.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "datasets" / "visual-dev-v0.1"

    result = snapshot_visual_dataset(version="visual-dev-v0.1", review_dir=review, output=output)

    assert result["detector_training_samples"] == 3
    assert (output / "analysis" / "visual_review_report.md").is_file()
    assert (output / "single_reviews" / "molecule-valid.json").is_file()
    summary = json.loads((output / "dataset_summary.json").read_text(encoding="utf-8"))
    assert summary["version"] == "visual-dev-v0.1"
    checksum_rows = (output / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    digest, relative = next(line.split("  ", 1) for line in checksum_rows if line.endswith("dataset_summary.json"))
    assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError, match="will not be overwritten"):
        snapshot_visual_dataset(version="visual-dev-v0.1", review_dir=review, output=output)


def test_holdout_snapshot_requires_zero_remaining_and_records_role_and_papers(tmp_path: Path) -> None:
    review = _review_fixture(tmp_path)
    analyze_visual_review(review)
    _write_csv(review / "visual_verified.csv", [{"sample_id": "molecule-valid"}])
    _write_csv(review / "visual_rejected.csv", [{"sample_id": "molecule-text"}])
    _write_csv(review / "missing_files.csv", [])
    (review / "review_consistency_report.json").write_text("{}", encoding="utf-8")
    papers = [{"pmcid": f"PMC{index}", "title": f"Paper {index}"} for index in range(1, 4)]
    (review / "holdout_papers.json").write_text(
        json.dumps({"papers": papers}), encoding="utf-8",
    )
    (review / "single_reviews" / "negative-smiles.json").unlink()
    with pytest.raises(ValueError, match="Visual remaining must be zero"):
        snapshot_visual_dataset(
            version="visual-holdout-v0.1", review_dir=review,
            output=tmp_path / "incomplete", dataset_role="holdout",
        )
    for row in _read_csv(review / "machine_review_manifest.csv"):
        if not (review / "single_reviews" / f"{row['sample_id']}.json").is_file():
            _audit(review, row, "invalid_crop", [10, 10, 90, 90])
    output = tmp_path / "visual-holdout-v0.1"
    snapshot_visual_dataset(
        version="visual-holdout-v0.1", review_dir=review,
        output=output, dataset_role="holdout",
    )
    summary = json.loads((output / "dataset_summary.json").read_text(encoding="utf-8"))
    assert summary["dataset_role"] == "holdout"
    assert len(summary["papers"]) == 3
    assert (output / "holdout_papers.json").is_file()
