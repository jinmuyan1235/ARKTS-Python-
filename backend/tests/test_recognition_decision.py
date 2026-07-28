"""Tests for the unified OCSR recognition decision layer."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from src.analysis.image_quality import assess_image_quality
from src.analysis.molecule_report import MoleculeReportGenerator
from src.analysis.recognition_decision import decide_recognition


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _base_report() -> dict:
    return {
        "input": {"type": "image", "filename": "mol.png"},
        "status": "success",
        "ocsr": {"backend": "molscribe", "status": "success", "smiles": "CCO", "predicted_smiles": "CCO", "confidence": 0.92},
        "validation": {"valid": True, "canonical_smiles": "CCO"},
        "chemical_identity": {"fragment_count": 1, "formal_charge": 0, "stereocenter_count": 0},
        "structure_warnings": [],
        "image_quality": {"quality_score": 0.88, "passed": True, "reason_codes": []},
    }


def test_single_uncalibrated_model_is_accepted_with_warning() -> None:
    decision = decide_recognition(_base_report())
    assert decision["decision"] == "accepted_with_warning"
    assert decision["manual_review_recommended"] is True
    assert "single_backend_only" in decision["reason_codes"]


def test_uncalibrated_single_model_requires_review_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.analysis.recognition_decision.config.DECISION_REQUIRE_CALIBRATED_CONFIDENCE",
        True,
    )
    decision = decide_recognition(_base_report())
    assert decision["decision"] == "review_needed"
    assert decision["manual_review_recommended"] is True
    assert "calibrated_confidence_missing" in decision["reason_codes"]


def test_multi_backend_agreement_is_not_automatic_verification() -> None:
    report = _base_report()
    report["ocsr"] = {
        "backend": "ensemble",
        "status": "success",
        "smiles": "CCO",
        "predicted_smiles": "CCO",
        "consensus": {"status": "agreement", "decision": "accepted", "reason_codes": ["multi_backend_agreement"]},
    }
    decision = decide_recognition(report)
    assert decision["decision"] == "accepted_with_warning"
    assert decision["risk_level"] == "medium"
    assert decision["manual_review_recommended"] is True
    assert "agreement_not_ground_truth" in decision["reason_codes"]


def test_single_valid_ensemble_candidate_requires_review_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.analysis.recognition_decision.config.DECISION_REQUIRE_CALIBRATED_CONFIDENCE",
        True,
    )
    report = _base_report()
    report["ocsr"] = {
        "backend": "ensemble",
        "status": "success",
        "smiles": "CCO",
        "predicted_smiles": "CCO",
        "consensus": {
            "status": "single_valid",
            "decision": "accepted_with_warning",
            "reason_codes": ["single_backend_only", "uncalibrated_confidence"],
        },
    }
    decision = decide_recognition(report)
    assert decision["decision"] == "review_needed"
    assert decision["manual_review_recommended"] is True
    assert "single_backend_only" in decision["reason_codes"]


def test_backend_disagreement_requires_review() -> None:
    report = _base_report()
    report["ocsr"] = {
        "backend": "ensemble",
        "status": "failed",
        "smiles": None,
        "consensus": {"status": "disagreement", "decision": "review_needed", "reason_codes": ["backend_disagreement"]},
    }
    decision = decide_recognition(report)
    assert decision["decision"] == "review_needed"
    assert "backend_disagreement" in decision["reason_codes"]


def test_low_quality_image_forces_review_even_when_smiles_is_valid() -> None:
    report = _base_report()
    report["image_quality"] = {"quality_score": 0.2, "passed": False, "reason_codes": ["blurred"]}
    decision = decide_recognition(report)
    assert decision["decision"] == "review_needed"
    assert "low_image_quality" in decision["reason_codes"]


def test_image_quality_detects_tiny_blank_input(tmp_path: Path) -> None:
    image_path = tmp_path / "blank.png"
    Image.new("RGB", (32, 32), "white").save(image_path)
    quality = assess_image_quality(image_path)
    assert quality["quality_score"] < 0.55
    assert "low_resolution" in quality["reason_codes"]


def test_demo_report_contains_decision_and_quality(tmp_path: Path) -> None:
    sample = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    report = MoleculeReportGenerator("demo", tmp_path).generate(image_path=sample)
    assert report["status"] == "success"
    assert report["image_quality"]["quality_score"] is not None
    assert report["recognition_decision"]["decision"] == "accepted_with_warning"
