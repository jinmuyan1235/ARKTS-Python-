"""Production OCSR routing, capability, UI, and audit safety tests."""

from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

import config
from src.ocsr.model_capabilities import load_model_capabilities
from src.ocsr.production_routing import (
    assert_no_prediction_as_ground_truth,
    build_recognition_audit,
    route_model_candidates,
)
from src.ui.model_capability_view import capability_panel_data, model_result_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _candidate(backend: str, smiles: str | None, canonical: str | None, inchikey: str | None = None, **extra):
    return {
        "backend": backend,
        "status": "success" if smiles else "failed",
        "valid": canonical is not None,
        "raw_smiles": smiles,
        "canonical_smiles": canonical,
        "inchikey": inchikey,
        "model_version": f"{backend}-v1",
        "model_sha256": f"{backend}-unchanged-hash",
        "inference_time_ms": 12.5,
        **extra,
    }


def test_decimer_is_selected_before_molscribe() -> None:
    route = route_model_candidates([
        _candidate("molscribe", "CCO", "CCO", "MOL"),
        _candidate("decimer", "c1ccccc1", "c1ccccc1", "DEC"),
    ])
    assert route["selected_backend"] == "decimer"
    assert route["decision"] == "primary_candidate"
    assert route["agreement_status"] == "model_disagreement"
    assert route["review_required"] is True


def test_molscribe_fallback_always_requires_review() -> None:
    route = route_model_candidates([
        _candidate("decimer", None, None),
        _candidate("molscribe", "CCO", "CCO", "MOL"),
    ])
    assert route["decision"] == "fallback_candidate"
    assert route["review_required"] is True
    assert route["property_analysis_allowed"] is False


def test_model_agreement_never_sets_verified() -> None:
    route = route_model_candidates([
        _candidate("decimer", "OCC", "CCO", "SAME"),
        _candidate("molscribe", "CCO", "CCO", "SAME"),
    ])
    assert route["agreement_status"] == "agreement"
    assert route["agreement_increases_trust"] is False
    assert route["structure_verified"] is False


def test_no_parseable_smiles_blocks_property_analysis() -> None:
    route = route_model_candidates([_candidate("decimer", None, None), _candidate("molscribe", None, None)])
    assert route["decision"] == "recognition_failed"
    assert route["property_analysis_allowed"] is False


@pytest.mark.parametrize(
    ("smiles", "expected"),
    [
        ("CCO.[Na+]", "salt_or_multifragment"),
        ("C[NH3+]", "formal_charge"),
        ("C[C@H](O)Cl", "stereochemistry"),
        ("[Fe]", "metal_coordination"),
    ],
)
def test_high_risk_structures_are_flagged(smiles: str, expected: str) -> None:
    route = route_model_candidates([_candidate("decimer", smiles, smiles, "X")])
    assert expected in route["risk_flags"]
    assert "high_risk_structure" in route["risk_flags"]


def test_capability_file_matches_production_configuration(monkeypatch) -> None:
    capabilities = load_model_capabilities()
    defaults = capabilities["production_defaults"]
    monkeypatch.setenv("APP_MODE", "production")
    monkeypatch.delenv("OCSR_BACKEND", raising=False)
    settings = config.load_settings()
    assert settings.ocsr_backend == defaults["primary_ocsr_backend"] == "decimer"
    assert defaults["proposal_config"] == "baseline"
    assert defaults["crop_screening_config"] == "candidate"
    assert defaults["decimer_profile"] == "raw"
    assert capabilities["models"]["ensemble"]["enabled_as_default"] is False
    assert capabilities["fine_tuning_enabled"] is False
    assert capabilities["models"]["molscribe"]["production_model_sha256"] == (
        "6f0df56fa32b5ffc21f8c7f311ef333da522f590bf5622e966c6bcb1f2d9ea1d"
    )


def test_ui_distinguishes_execution_parse_and_verification() -> None:
    route = route_model_candidates([_candidate("decimer", "CCO", "CCO", "X")])
    audit = build_recognition_audit(route)
    status = model_result_status({"production_routing": route, "recognition_audit": audit})
    assert status["candidate_role"] == "Primary model candidate"
    assert status["backend_execution_succeeded"] is True
    assert status["valid_smiles_produced"] is True
    assert status["structure_verified"] is False
    assert status["prediction_notice"] == "Prediction is not verified ground truth"
    assert capability_panel_data()["real_pmc_accuracy_verified"] is False


def test_model_prediction_audit_cannot_claim_ground_truth() -> None:
    route = route_model_candidates([_candidate("decimer", "CCO", "CCO", "X")])
    audit = build_recognition_audit(route)
    assert set(audit) >= {
        "backend", "model_version", "model_hash", "input_profile", "execution_status",
        "parse_status", "candidate_smiles", "canonical_smiles", "inchikey", "risk_flags",
        "agreement_status", "review_required", "dataset_capability_version", "latency_ms", "timestamp",
    }
    assert audit["model_hash"] == "decimer-unchanged-hash"
    assert "ground_truth_smiles" not in audit
    assert "verified" not in audit
    assert audit["structure_verified"] is False
    assert_no_prediction_as_ground_truth(audit)
    with pytest.raises(ValueError):
        assert_no_prediction_as_ground_truth({**audit, "ground_truth_smiles": "CCO"})


def test_training_outputs_and_models_remain_gitignored() -> None:
    patterns = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/evaluation/" in patterns
    assert "data/datasets/" in patterns
    assert "models/*" in patterns


def test_original_molscribe_weight_hash_is_unchanged_when_present() -> None:
    checkpoint = config.MOLSCRIBE_MODEL_PATH
    if not checkpoint.is_file():
        pytest.skip("production checkpoint is intentionally absent from this test environment")
    digest = hashlib.sha256()
    with checkpoint.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    expected = load_model_capabilities()["models"]["molscribe"]["production_model_sha256"]
    assert digest.hexdigest() == expected
