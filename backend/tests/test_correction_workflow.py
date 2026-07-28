"""Tests for human correction workflow on image OCSR reports."""

from __future__ import annotations

import json
from pathlib import Path

from src.analysis.correction import (
    apply_strategy_attempt_result,
    apply_smiles_correction,
    restore_original_prediction,
    save_correction_feedback,
)
from src.analysis.molecule_report import MoleculeReportGenerator
from src.export.json_exporter import to_json_text
from src.export.pdf_exporter import save_pdf
from src.feedback.review_service import FeedbackReviewService
from src.feedback.store import export_feedback_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_sample(tmp_path: Path, name: str = "aspirin.png") -> Path:
    source = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    target = tmp_path / name
    target.write_bytes(source.read_bytes())
    return target


def test_valid_prediction_can_be_corrected_to_another_valid_smiles(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    corrected = apply_smiles_correction(report, "CCO", tmp_path)
    assert corrected["correction"]["applied"] is True
    assert corrected["correction"]["corrected_canonical_smiles"] == "CCO"
    assert corrected["final"]["smiles"] == "CCO"
    assert corrected["final"]["source"] == "user_correction"
    assert corrected["ocsr"]["predicted_smiles"] == report["ocsr"]["predicted_smiles"]
    assert corrected["descriptors"]["formula"] != report["descriptors"]["formula"]
    assert Path(corrected["images"]["corrected_molecule"]).is_file()


def test_invalid_correction_does_not_overwrite_valid_result(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    invalid = apply_smiles_correction(report, "not-a-smiles", tmp_path)
    assert invalid["correction"]["applied"] is False
    assert invalid["correction"]["last_error"]
    assert invalid["final"] == report["final"]
    assert invalid["descriptors"] == report["descriptors"]


def test_ocsr_failure_allows_manual_supplement(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "unknown.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    assert report["status"] == "failed"
    corrected = apply_smiles_correction(report, "CCO", tmp_path)
    assert corrected["status"] == "success"
    assert corrected["final"]["source"] == "manual_after_ocsr_failure"
    assert corrected["validation"]["valid"] is True
    assert Path(corrected["images"]["corrected_molecule"]).is_file()


def test_restore_original_prediction_keeps_prediction_trace(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    corrected = apply_smiles_correction(report, "CCO", tmp_path)
    restored = restore_original_prediction(corrected, tmp_path)
    assert restored["correction"]["applied"] is False
    assert restored["final"]["source"] == "ocsr"
    assert restored["final"]["canonical_smiles"] == report["final"]["canonical_smiles"]
    assert restored["ocsr"]["predicted_smiles"] == report["ocsr"]["predicted_smiles"]


def test_cached_strategy_attempt_can_be_applied_as_final_result(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    report["ocsr"]["strategy_attempts"] = [{
        "strategy": "binary",
        "status": "success",
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "valid_smiles": True,
        "confidence": 0.88,
        "message": "binary done",
    }]

    selected = apply_strategy_attempt_result(report, "binary", tmp_path)

    assert selected["status"] == "success"
    assert selected["final"]["source"] == "strategy_selection"
    assert selected["final"]["smiles"] == "CCO"
    assert selected["ocsr"]["selected_strategy"] == "binary"
    assert selected["strategy_selection"]["applied"] is True
    assert Path(selected["images"]["predicted_molecule"]).is_file()


def test_json_and_pdf_include_correction_fields(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    corrected = apply_smiles_correction(report, "CCO", tmp_path)
    data = json.loads(to_json_text(corrected))
    assert data["ocsr"]["predicted_smiles"]
    assert data["correction"]["corrected_smiles"] == "CCO"
    assert data["final"]["smiles"] == "CCO"
    pdf = save_pdf(corrected, tmp_path / "corrected_report.pdf")
    assert pdf["success"] is True
    assert Path(pdf["path"]).is_file()


def test_feedback_file_only_created_when_explicitly_saved(tmp_path: Path) -> None:
    image = _copy_sample(tmp_path, "aspirin.png")
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=image)
    corrected = apply_smiles_correction(report, "CCO", tmp_path)
    feedback_dir = tmp_path / "feedback"
    assert not feedback_dir.exists()
    result = save_correction_feedback(
        corrected,
        tmp_path,
        notes="unit note",
        correction_type="atom",
        source_reference="unit-source",
        source_license="internal-test",
    )
    payload = json.loads(Path(result["annotation_path"]).read_text(encoding="utf-8"))
    assert payload["correction"]["corrected_smiles"] == "CCO"
    assert payload["prediction"]["backend"] == "demo"
    assert payload["feedback"]["notes"] == "unit note"
    assert payload["feedback"]["correction_type"] == "atom"
    assert Path(result["image_path"]).is_file()
    assert (feedback_dir / "manifest.csv").is_file()


def test_feedback_detects_duplicate_images_and_exports_manifest(tmp_path: Path) -> None:
    first = MoleculeReportGenerator("demo", tmp_path).generate(image_path=_copy_sample(tmp_path, "aspirin_a.png"))
    second = MoleculeReportGenerator("demo", tmp_path).generate(image_path=_copy_sample(tmp_path, "aspirin_b.png"))
    first_corrected = apply_smiles_correction(first, "CCO", tmp_path)
    second_corrected = apply_smiles_correction(second, "CCO", tmp_path)

    first_result = save_correction_feedback(
        first_corrected,
        tmp_path,
        correction_type="other",
        review_status="pending",
        feedback_action="correction_only",
        include_in_training=False,
    )
    second_result = save_correction_feedback(
        second_corrected,
        tmp_path,
        correction_type="other",
        review_status="pending",
        feedback_action="correction_only",
        include_in_training=False,
    )

    assert first_result["duplicate_image"] is False
    assert second_result["duplicate_image"] is True
    service = FeedbackReviewService(tmp_path)
    service.approve_for_dataset(first_corrected["analysis_id"], reviewer="reviewer-a")
    service.approve_for_dataset(second_corrected["analysis_id"], reviewer="reviewer-a")
    output_manifest = tmp_path / "feedback_train_manifest.csv"
    export = export_feedback_manifest(tmp_path / "feedback", output_manifest)
    assert export["exported_count"] == 1
    rows = output_manifest.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert "feedback_" in rows[1]


def test_different_analysis_ids_do_not_share_correction_outputs(tmp_path: Path) -> None:
    first = MoleculeReportGenerator("demo", tmp_path).generate(image_path=_copy_sample(tmp_path, "aspirin_a.png"))
    second = MoleculeReportGenerator("demo", tmp_path).generate(image_path=_copy_sample(tmp_path, "aspirin_b.png"))
    assert first["analysis_id"] != second["analysis_id"]
    first_corrected = apply_smiles_correction(first, "CCO", tmp_path)
    second_corrected = apply_smiles_correction(second, "c1ccccc1", tmp_path)
    assert first_corrected["final"]["smiles"] == "CCO"
    assert second_corrected["final"]["smiles"] == "c1ccccc1"
    assert first_corrected["images"]["corrected_molecule"] != second_corrected["images"]["corrected_molecule"]
