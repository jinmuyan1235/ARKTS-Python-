"""Tests for the optional, failure-isolated ADMET baseline."""

from pathlib import Path

import pytest

from src.ml.admet_baseline import ADMETBaseline, ConfiguredADMETPredictor, smiles_to_fingerprint


def test_morgan_fingerprint_shape_and_invalid_input() -> None:
    fingerprint = smiles_to_fingerprint("CCO", n_bits=256)
    assert fingerprint.shape == (256,)
    assert set(fingerprint.tolist()) <= {0, 1}
    with pytest.raises(ValueError, match="SMILES"):
        smiles_to_fingerprint("not_a_smiles")


def test_configured_predictor_is_disabled_by_default(tmp_path: Path) -> None:
    result = ConfiguredADMETPredictor(enabled=False, model_path=tmp_path / "missing.joblib").predict("CCO")
    assert result["status"] == "disabled"
    assert "RDKit" in result["message"]


def test_train_save_load_and_predict_classification(tmp_path: Path) -> None:
    class_zero = [
        "CCO", "CCCO", "CCCCO", "CCN", "CCCN", "CCCCN", "CCC", "CCCC", "CC(C)O", "CC(C)N",
        "CCOC", "CCCOC", "CCS", "CCCS", "CC(C)C",
    ]
    class_one = [
        "c1ccccc1", "Cc1ccccc1", "Oc1ccccc1", "Nc1ccccc1", "Clc1ccccc1", "Brc1ccccc1",
        "c1ccncc1", "c1ccccc1O", "c1ccccc1N", "c1ccccc1Cl", "CC(=O)c1ccccc1", "COc1ccccc1",
        "CCc1ccccc1", "FC1=CC=CC=C1", "c1ccc(Cl)cc1",
    ]
    smiles = class_zero + class_one
    labels = [0] * len(class_zero) + [1] * len(class_one)
    model = ADMETBaseline.train(
        smiles,
        labels,
        target_name="ames",
        random_state=7,
        split_strategy="random",
        min_samples=20,
    )
    model_path = model.save(tmp_path / "ames.joblib")
    loaded = ADMETBaseline.load(model_path)
    result = loaded.predict("CCO")
    assert result["status"] == "success"
    assert result["target"] == "ames"
    assert result["prediction"] in {0, 1}
    assert 0 <= result["probability"] <= 1
    assert result["validation_samples"] >= 4
    assert result["metrics"]["f1_macro"] >= 0.5
    assert result["quality_gate"]["passed"] is True
    assert "nearest_training_tanimoto" in result["applicability_domain"]


def test_admet_rejects_tiny_training_sets() -> None:
    with pytest.raises(ValueError, match="至少需要 20 条"):
        ADMETBaseline.train(["CCO", "CCN", "CCC", "CCCl"], [0, 0, 1, 1], target_name="ames")


def test_enabled_predictor_reports_missing_model(tmp_path: Path) -> None:
    result = ConfiguredADMETPredictor(enabled=True, model_path=tmp_path / "missing.joblib").predict("CCO")
    assert result["status"] == "unavailable"
    assert "不存在" in result["message"]
