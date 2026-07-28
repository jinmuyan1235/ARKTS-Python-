from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from src.datasets.trusted_ocsr import MANIFEST_FIELDS, sha256_file
from src.datasets.trusted_ocsr_v2 import (
    ExternalHoldoutBuildConfig, TrustedOCSRExternalHoldoutBuilder, _typed_perturbation,
    validate_external_holdout,
)
from src.evaluation.ensemble_dev_analysis import analyze_development_overlap
from src.evaluation.preprocessing_experiment import run_preprocessing_experiment
from src.evaluation.trusted_ocsr import evaluate_prediction, evaluate_trusted_manifest, summarize_predictions
from src.ocsr.base import OCSRResult
from src.ocsr.candidate_router import build_router_features
from src.ocsr.input_normalization import get_profile, normalize_ocsr_input
from src.ocsr.reliability import classify_backend_failure, run_with_single_retry

from tests.test_trusted_ocsr_dataset import _trusted_fixture


def _rewrite_manifest_split(manifest: Path, split: str) -> None:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["split"] = split
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS); writer.writeheader(); writer.writerows(rows)
    root = manifest.parent
    if split == "external_holdout":
        (root / "protocol.json").write_text(json.dumps({
            "dataset_role": "external_holdout",
            "model_results_viewed_before_freeze": False,
            "frozen_profiles": {},
        }), encoding="utf-8")
    checksum_lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "checksums.sha256")
    ]
    (root / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def test_frozen_test_cannot_participate_in_profile_selection(tmp_path: Path):
    with pytest.raises(ValueError, match="Frozen test"):
        run_preprocessing_experiment(tmp_path / "missing.csv", tmp_path / "out", splits=("test",))


def test_output_parse_failure_is_not_inference_failure():
    result = OCSRResult(
        None, None, "molscribe", "failed",
        "MolScribe returned unparsable structure string: C1=broken",
        raw_output="C1=broken",
    )
    assert classify_backend_failure(result) == "output_parse_failure"
    evaluated = evaluate_prediction({"ground_truth_isomeric_smiles": "CCO"}, result, 1.0)
    assert evaluated["backend_execution_success"] is True
    assert evaluated["backend_success"] is False
    assert evaluated["error_type"] == "output_parse_failure"


@pytest.mark.parametrize(("message", "category"), (
    ("worker subprocess returned code 7", "subprocess_failure"),
    ("operation timed out", "timeout"),
    ("CUDA out of memory", "cuda_failure"),
    ("cannot identify image file", "image_decode_failure"),
    ("unsupported image array dimensions", "input_preprocessing_failure"),
    ("ModuleNotFoundError: missing dependency", "dependency_failure"),
    ("model inference failed", "model_inference_failure"),
    ("模型返回了无法解析的结构字符串", "output_parse_failure"),
))
def test_backend_failure_taxonomy(message: str, category: str):
    result = OCSRResult(None, None, "molscribe", "failed", message)
    assert classify_backend_failure(result) == category


def test_retry_does_not_duplicate_success_or_retry_parse_failure():
    calls = {"primary": 0, "retry": 0}

    def primary(_image):
        calls["primary"] += 1
        return OCSRResult("CCO", 1.0, "molscribe", "success", "ok")

    def retry(_backend, _image):
        calls["retry"] += 1
        return OCSRResult("CCC", 1.0, "molscribe", "success", "retry")

    result = run_with_single_retry("molscribe", object(), primary, retry)
    assert result.smiles == "CCO" and result.attempt_count == 1
    assert calls == {"primary": 1, "retry": 0}

    parse = run_with_single_retry(
        "molscribe", object(),
        lambda _image: OCSRResult(None, None, "molscribe", "failed", "unparsable", raw_output="bad"),
        retry,
    )
    assert parse.failure_category == "output_parse_failure" and parse.attempt_count == 1
    assert calls["retry"] == 0

    dependency = run_with_single_retry(
        "decimer", object(),
        lambda _image: OCSRResult(
            None, None, "decimer", "failed", "missing dependency",
            failure_category="dependency_failure",
        ),
        retry,
    )
    assert dependency.failure_category == "dependency_failure" and dependency.attempt_count == 1
    assert calls["retry"] == 0


def test_overall_and_conditional_metrics_are_distinct():
    truth = {"ground_truth_isomeric_smiles": "CCO"}
    correct = evaluate_prediction(truth, OCSRResult("CCO", 1.0, "molscribe", "success", "ok"), 1.0)
    parse = evaluate_prediction(
        truth, OCSRResult(None, None, "molscribe", "failed", "unparsable", raw_output="C1="), 1.0,
    )
    metrics = summarize_predictions([correct, parse])
    assert metrics["full_inchikey_exact_rate"] == 0.5
    assert metrics["conditional_full_inchikey_exact_rate"] == 1.0
    assert metrics["backend_execution_success_rate"] == 1.0
    assert metrics["backend_success_rate"] == 0.5


def test_alpha_flatten_and_autocrop_preserve_structure(tmp_path: Path):
    alpha = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    alpha.putpixel((20, 20), (0, 0, 0, 255))
    alpha_path = tmp_path / "alpha.png"; alpha.save(alpha_path)
    flattened = normalize_ocsr_input(alpha_path, "alpha_flatten")
    assert tuple(flattened[0, 0]) == (255, 255, 255)
    assert tuple(flattened[20, 20]) == (0, 0, 0)

    structure = Image.new("RGB", (30, 30), "white")
    for index in range(30):
        structure.putpixel((index, index), (0, 0, 0))
    cropped = normalize_ocsr_input(structure, get_profile("autocrop_and_pad", crop_safety_px=4))
    assert np.count_nonzero(np.all(cropped < 20, axis=2)) >= 30
    assert np.all(cropped[0, 0] == 255) and np.all(cropped[-1, -1] == 255)


def test_normalized_profile_is_materialized_as_temporary_image_path(tmp_path: Path):
    manifest = _trusted_fixture(tmp_path / "dataset", smiles="CCO")
    seen: list[Path] = []

    def predictor(path: Path) -> OCSRResult:
        assert isinstance(path, Path) and path.is_file() and path.suffix == ".png"
        assert "ocsr_molscribe_alpha_flatten_" in str(path.parent)
        seen.append(path)
        return OCSRResult("CCO", 1.0, "molscribe", "success", "ok")

    evaluate_trusted_manifest(
        manifest, "molscribe", tmp_path / "evaluation", predictor=predictor,
        preprocessing_profile="alpha_flatten",
    )
    assert len(seen) == 3
    assert all(not path.exists() for path in seen)


def test_typed_perturbation_is_reproducible_and_traceable():
    image = Image.new("RGB", (64, 64), "white")
    first, parameters, severity = _typed_perturbation(image, "noise", 123)
    second, parameters_again, severity_again = _typed_perturbation(image, "noise", 123)
    assert parameters == parameters_again
    assert parameters["type"] == "noise" and parameters["source_layer"] == "official_clean"
    assert parameters["seed"] == 123 and parameters["severity"] == severity == severity_again
    assert np.array_equal(np.asarray(first), np.asarray(second))


def test_candidate_router_rejects_ground_truth_features():
    with pytest.raises(ValueError, match="ground-truth"):
        build_router_features({"ground_truth_formula": "C2H6O"})


def test_ensemble_dev_analysis_rejects_frozen_test(tmp_path: Path):
    fields = ("sample_id", "split", "inchikey_exact_match", "predicted_inchikey")
    for name in ("molscribe.csv", "decimer.csv"):
        with (tmp_path / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
            writer.writerow({"sample_id": "x", "split": "test", "inchikey_exact_match": True, "predicted_inchikey": "A"})
    with pytest.raises(ValueError, match="Frozen test"):
        analyze_development_overlap(tmp_path / "molscribe.csv", tmp_path / "decimer.csv", tmp_path / "out")


def test_v2_validation_detects_v1_identity_leakage(tmp_path: Path):
    v1 = tmp_path / "v1"; _trusted_fixture(v1)
    v2 = tmp_path / "v2"; shutil.copytree(v1, v2)
    _rewrite_manifest_split(v2 / "manifest.csv", "external_holdout")
    result = validate_external_holdout(v2, v1)
    assert not result["valid"]
    assert any("v0.1_pubchem_cid_leakage" in error for error in result["errors"])
    assert any("v0.1_ground_truth_inchikey_leakage" in error for error in result["errors"])


def test_v2_snapshot_refuses_overwrite_before_network_access(tmp_path: Path):
    output = tmp_path / "ocsr-trusted-v0.2"; output.mkdir()
    config = ExternalHoldoutBuildConfig(
        output=output, cache_dir=tmp_path / "cache",
        reference_manifest=tmp_path / "missing-v1.csv",
        frozen_profiles=tmp_path / "missing-profiles.json",
    )
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        TrustedOCSRExternalHoldoutBuilder(config).build()


def test_external_holdout_formal_output_cannot_run_twice(tmp_path: Path):
    manifest = _trusted_fixture(tmp_path / "dataset", smiles="CCO")
    _rewrite_manifest_split(manifest, "external_holdout")
    output = tmp_path / "evaluation" / "molscribe_raw"
    predictor = lambda _path: OCSRResult("CCO", 1.0, "molscribe", "success", "ok")
    evaluate_trusted_manifest(
        manifest, "molscribe", output, predictor=predictor,
        splits=("external_holdout",), purpose="external_holdout",
    )
    with pytest.raises(FileExistsError, match="already frozen"):
        evaluate_trusted_manifest(
            manifest, "molscribe", output, predictor=predictor,
            splits=("external_holdout",), purpose="external_holdout",
        )
