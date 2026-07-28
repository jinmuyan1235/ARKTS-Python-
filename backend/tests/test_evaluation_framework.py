"""Tests for the standalone OCSR benchmark framework."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from src.evaluation.dataset import ManifestValidationError, load_manifest
from src.evaluation.evaluator import OCSREvaluator
from src.evaluation.metrics import compute_metrics, enrich_prediction, tanimoto_similarity
from src.evaluation.release_compare import compare_release_dirs, write_comparison_report
from src.evaluation.release_gate import collect_release_error_rows, evaluate_release_gates
from src.evaluation.report_writer import create_run_directory, write_report_bundle
from src.ocsr.base import BaseOCSRAdapter, OCSRResult
from src.ocsr.recognizer import MoleculeRecognizer
from scripts.run_release_acceptance import run_release_acceptance


def _image(path: Path) -> Path:
    Image.new("RGB", (24, 24), "white").save(path)
    return path


def _manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    fieldnames = ["sample_id", "image_path", "ground_truth_smiles", "category", "source", "notes"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_manifest(path: Path, image_path: Path, **overrides: str) -> Path:
    row = {
        "sample_id": "ethanol_real",
        "image_path": image_path.name,
        "dataset_version": "v-test",
        "image_sha256": _sha256(image_path),
        "ground_truth_smiles": "CCO",
        "ground_truth_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "expected_action": "recognize",
        "supported_scope": "single_molecule",
        "category": "clear_single_molecule",
        "source": "unit",
        "source_document": "unit-doc",
        "source_license": "internal-test",
        "annotator": "unit-annotator",
        "reviewer": "unit-reviewer",
        "review_status": "verified",
        "split": "test",
        "scaffold_key": "alcohol",
        "image_quality": "clean",
        "complexity": "low",
        "perturbation": "none",
        "structure_features": "alcohol",
        "notes": "strict manifest test",
    }
    row.update(overrides)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_manifest_loads_valid_rows(tmp_path: Path) -> None:
    _image(tmp_path / "aspirin.png")
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [{
            "sample_id": "aspirin_001",
            "image_path": "aspirin.png",
            "ground_truth_smiles": "CC(=O)Oc1ccccc1C(=O)O",
            "category": "clean",
            "source": "unit",
            "notes": "ok",
        }],
    )
    samples = load_manifest(manifest, tmp_path)
    assert len(samples) == 1
    assert samples[0].ground_truth_canonical_smiles
    assert samples[0].image_quality == "unspecified"


def test_manifest_loads_recommended_acceptance_metadata(tmp_path: Path) -> None:
    _image(tmp_path / "aspirin.png")
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "image_path",
                "ground_truth_smiles",
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
            ],
        )
        writer.writeheader()
        writer.writerow({
            "sample_id": "aspirin_scan",
            "image_path": "aspirin.png",
            "ground_truth_smiles": "CC(=O)Oc1ccccc1C(=O)O",
            "category": "literature_scan",
            "source": "paper",
            "split": "test",
            "scaffold_key": "benzene_carboxylate",
            "source_document": "paper-1",
            "image_quality": "scanned",
            "complexity": "medium",
            "perturbation": "jpeg_compression",
            "structure_features": "ester;acid;aromatic",
            "notes": "metadata test",
        })
    sample = load_manifest(manifest, tmp_path)[0]
    assert sample.image_quality == "scanned"
    assert sample.scaffold_key == "benzene_carboxylate"


def test_real_acceptance_manifest_requires_integrity_and_review_metadata(tmp_path: Path) -> None:
    image = _image(tmp_path / "ethanol.png")
    manifest = _real_manifest(tmp_path / "manifest.csv", image)
    sample = load_manifest(manifest, tmp_path, require_real_metadata=True)[0]
    assert sample.dataset_version == "v-test"
    assert sample.image_sha256 == _sha256(image)
    assert sample.review_status == "verified"
    assert sample.ground_truth_inchikey == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"

    bad_manifest = _real_manifest(tmp_path / "bad_manifest.csv", image, image_sha256="0" * 64)
    try:
        load_manifest(bad_manifest, tmp_path, require_real_metadata=True)
    except ManifestValidationError as exc:
        assert "image_sha256 mismatch" in str(exc)
    else:
        raise AssertionError("Expected checksum validation failure")

    missing_reviewer = _real_manifest(tmp_path / "missing_reviewer.csv", image, reviewer="")
    try:
        load_manifest(missing_reviewer, tmp_path, require_real_metadata=True)
    except ManifestValidationError as exc:
        assert "reviewer" in str(exc)
    else:
        raise AssertionError("Expected missing reviewer validation failure")


def test_real_acceptance_manifest_rejects_source_document_split_leakage(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png")
    second = _image(tmp_path / "second.png")
    rows = []
    for sample_id, image_path, split in (("first", first, "train"), ("second", second, "test")):
        rows.append({
            "sample_id": sample_id,
            "image_path": image_path.name,
            "dataset_version": "v-test",
            "image_sha256": _sha256(image_path),
            "ground_truth_smiles": "CCO",
            "ground_truth_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            "expected_action": "recognize",
            "supported_scope": "single_molecule",
            "category": "clear_single_molecule",
            "source": "unit",
            "source_document": "shared-source-doc",
            "source_license": "internal-test",
            "annotator": "unit-annotator",
            "reviewer": "unit-reviewer",
            "review_status": "verified",
            "split": split,
            "scaffold_key": "alcohol",
            "image_quality": "clean",
            "complexity": "low",
            "perturbation": "none",
            "structure_features": "alcohol",
            "notes": "split leakage test",
        })
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        load_manifest(manifest, tmp_path, require_real_metadata=True)
    except ManifestValidationError as exc:
        assert "multiple splits" in str(exc)
    else:
        raise AssertionError("Expected source_document split validation failure")


def test_manifest_allows_reject_rows_without_ground_truth_smiles(tmp_path: Path) -> None:
    _image(tmp_path / "table.png")
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "image_path",
                "ground_truth_smiles",
                "expected_action",
                "category",
                "source",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "sample_id": "reject_table",
            "image_path": "table.png",
            "ground_truth_smiles": "",
            "expected_action": "reject",
            "category": "table_distractor",
            "source": "unit",
            "notes": "negative control",
        })
    sample = load_manifest(manifest, tmp_path)[0]
    assert sample.expected_action == "reject"
    assert sample.ground_truth_canonical_smiles is None


def test_acceptance_builder_outputs_valid_manifest(tmp_path: Path) -> None:
    from scripts.build_ocsr_acceptance_set import build_acceptance_set

    seed = tmp_path / "seed.csv"
    with seed.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", "smiles", "category", "source", "split", "scaffold_key", "source_document", "complexity", "structure_features", "notes"],
        )
        writer.writeheader()
        writer.writerow({
            "sample_id": "ethanol",
            "smiles": "CCO",
            "category": "clean_generated",
            "source": "unit",
            "split": "test",
            "scaffold_key": "alcohol",
            "source_document": "unit",
            "complexity": "low",
            "structure_features": "alcohol",
            "notes": "builder test",
        })
    manifest = build_acceptance_set(
        seed_path=seed,
        output_root=tmp_path / "acceptance",
        variants=["clean", "low_res"],
        size=(256, 256),
        include_distractors=True,
        random_seed=1,
    )
    samples = load_manifest(manifest, tmp_path / "acceptance")
    assert len(samples) == 5
    assert {sample.expected_action for sample in samples} == {"recognize", "reject"}


def test_manifest_detects_missing_image_invalid_smiles_and_duplicate_ids(tmp_path: Path) -> None:
    _image(tmp_path / "one.png")
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [
            {
                "sample_id": "dup",
                "image_path": "missing.png",
                "ground_truth_smiles": "CCO",
                "category": "clean",
                "source": "unit",
                "notes": "",
            },
            {
                "sample_id": "dup",
                "image_path": "one.png",
                "ground_truth_smiles": "not-a-smiles",
                "category": "clean",
                "source": "unit",
                "notes": "",
            },
        ],
    )
    try:
        load_manifest(manifest, tmp_path)
    except ManifestValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected manifest validation failure")
    assert "image file does not exist" in message
    assert "duplicate sample_id" in message
    assert "invalid ground_truth_smiles" in message


def test_manifest_detects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    _image(outside)
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [{
            "sample_id": "escape",
            "image_path": "../outside.png",
            "ground_truth_smiles": "CCO",
            "category": "clean",
            "source": "unit",
            "notes": "",
        }],
    )
    try:
        load_manifest(manifest, tmp_path)
    except ManifestValidationError as exc:
        assert "escapes dataset root" in str(exc)
    else:
        raise AssertionError("Expected path escape validation failure")


def test_canonical_exact_and_equivalent_smiles() -> None:
    row = enrich_prediction(
        {
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "OCC",
            "recognition_success": True,
            "failure_reason": "",
        },
        similarity_threshold=0.95,
    )
    assert row["rdkit_valid"] is True
    assert row["canonical_exact_match"] is True
    assert row["molecule_equivalent"] is True


def test_invalid_prediction_and_similarity() -> None:
    row = enrich_prediction(
        {
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "not-a-smiles",
            "recognition_success": True,
            "failure_reason": "",
        },
        similarity_threshold=0.95,
    )
    assert row["rdkit_valid"] is False
    assert row["failure_reason"] == "invalid_predicted_smiles"
    assert tanimoto_similarity("CCO", "CCO") == 1.0


class FakeSuccessAdapter(BaseOCSRAdapter):
    backend_name = "fake_success"
    preferred_image_stage = "original"

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        return OCSRResult("OCC", 0.9, self.backend_name, "success", "ok", inference_time_ms=12.5)


class FakeErrorAdapter(BaseOCSRAdapter):
    backend_name = "fake_error"

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        raise RuntimeError("backend boom")


def test_evaluator_mock_adapter_success_and_latency(monkeypatch, tmp_path: Path) -> None:
    image = _image(tmp_path / "ethanol.png")
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [{
            "sample_id": "ethanol",
            "image_path": "ethanol.png",
            "ground_truth_smiles": "CCO",
            "category": "clean",
            "source": "unit",
            "notes": "",
        }],
    )
    monkeypatch.setitem(MoleculeRecognizer.ADAPTERS, "fake_success", FakeSuccessAdapter)
    sample = load_manifest(manifest, tmp_path)[0]
    result = OCSREvaluator("fake_success").run([sample])
    row = result["rows"][0]
    assert row["image_path"] == str(image.resolve())
    assert row["canonical_exact_match"] is True
    assert result["metrics"]["overall"]["mean_latency_ms"] == 12.5


def test_evaluator_backend_exception_continues(monkeypatch, tmp_path: Path) -> None:
    _image(tmp_path / "ethanol.png")
    manifest = _manifest(
        tmp_path / "manifest.csv",
        [{
            "sample_id": "ethanol",
            "image_path": "ethanol.png",
            "ground_truth_smiles": "CCO",
            "category": "clean",
            "source": "unit",
            "notes": "",
        }],
    )
    monkeypatch.setitem(MoleculeRecognizer.ADAPTERS, "fake_error", FakeErrorAdapter)
    sample = load_manifest(manifest, tmp_path)[0]
    result = OCSREvaluator("fake_error").run([sample])
    assert result["rows"][0]["recognition_success"] is False
    assert "未预期错误" in result["rows"][0]["message"]


def test_metrics_grouping_and_latency_statistics() -> None:
    rows = [
        enrich_prediction({
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "category": "clean",
            "backend": "fake",
            "preprocessing_strategy": "original",
            "inference_time_ms": 10,
        }, 0.95),
        enrich_prediction({
            "ground_truth_smiles": "CCO",
            "predicted_smiles": None,
            "expected_action": "reject",
            "recognition_success": False,
            "category": "noisy",
            "backend": "fake",
            "preprocessing_strategy": "original",
            "inference_time_ms": 30,
            "failure_reason": "no_smiles",
        }, 0.95),
    ]
    metrics = compute_metrics(rows)
    assert metrics["overall"]["total_samples"] == 2
    assert metrics["overall"]["recognition_success_count"] == 1
    assert metrics["overall"]["valid_smiles_count"] == 1
    assert metrics["overall"]["atom_count_error_rate"] == 0.0
    assert metrics["overall"]["rejection_coverage"] == 1.0
    assert metrics["overall"]["false_accept_rate"] == 0.0
    assert metrics["overall"]["review_needed_count"] == 0
    assert metrics["overall"]["p50_latency_ms"] == 10.0
    assert metrics["overall"]["median_latency_ms"] == 20.0
    assert "clean" in metrics["groups"]["category"]
    assert "unknown" in metrics["groups"]["image_quality"]


def test_rejection_metrics_distinguish_reviewed_hallucination_from_false_accept() -> None:
    rows = [
        enrich_prediction({
            "sample_id": "document_context_recognize",
            "expected_action": "recognize",
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "recognition_decision": "accepted",
            "manual_review_recommended": False,
            "structure_features": "document_text",
            "category": "real_document_full_crop",
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
        enrich_prediction({
            "sample_id": "negative_reviewed",
            "expected_action": "reject",
            "ground_truth_smiles": "",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "recognition_decision": "accepted_with_warning",
            "manual_review_recommended": True,
            "category": "text_only_negative",
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
        enrich_prediction({
            "sample_id": "negative_auto_accepted",
            "expected_action": "reject",
            "ground_truth_smiles": "",
            "predicted_smiles": "CCN",
            "recognition_success": True,
            "recognition_decision": "accepted",
            "manual_review_recommended": False,
            "category": "text_only_negative",
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
    ]
    metrics = compute_metrics(rows)["overall"]
    assert metrics["rejection_target_count"] == 2
    assert metrics["negative_hallucination_count"] == 2
    assert metrics["false_accept_count"] == 1
    assert metrics["false_accept_rate"] == 0.5

    errors = collect_release_error_rows(rows, "fake")
    reasons = {row["sample_id"]: row["failure_reason"] for row in errors}
    assert reasons["negative_reviewed"] == "negative_hallucination_review_required"
    assert reasons["negative_auto_accepted"] == "false_accept"


def test_positive_metrics_exclude_correctly_rejected_negative_samples() -> None:
    rows = [
        enrich_prediction({
            "sample_id": "positive_correct",
            "expected_action": "recognize",
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "OCC",
            "recognition_success": True,
            "recognition_decision": "accepted",
            "manual_review_recommended": False,
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
        enrich_prediction({
            "sample_id": "negative_rejected",
            "expected_action": "reject",
            "ground_truth_smiles": "",
            "predicted_smiles": None,
            "recognition_success": False,
            "recognition_decision": "rejected",
            "manual_review_recommended": True,
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
    ]
    metrics = compute_metrics(rows)["overall"]
    assert metrics["positive_sample_count"] == 1
    assert metrics["negative_sample_count"] == 1
    assert metrics["valid_smiles_rate"] == 1.0
    assert metrics["canonical_exact_match_rate"] == 1.0
    assert metrics["molecule_equivalent_rate"] == 1.0
    assert metrics["rejection_coverage"] == 1.0


def test_negative_hallucination_does_not_raise_positive_valid_rate() -> None:
    rows = [
        enrich_prediction({
            "sample_id": "positive_failed",
            "expected_action": "recognize",
            "ground_truth_smiles": "CCO",
            "predicted_smiles": None,
            "recognition_success": False,
            "recognition_decision": "rejected",
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
        enrich_prediction({
            "sample_id": "negative_hallucinated",
            "expected_action": "reject",
            "ground_truth_smiles": "",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "recognition_decision": "accepted_with_warning",
            "manual_review_recommended": True,
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95),
    ]
    metrics = compute_metrics(rows)["overall"]
    assert metrics["valid_smiles_rate"] == 0.0
    assert metrics["valid_smiles_count"] == 0
    assert metrics["negative_hallucination_count"] == 1
    assert metrics["negative_hallucination_rate"] == 1.0
    assert metrics["false_accept_count"] == 0
    assert metrics["false_accept_rate"] == 0.0


def test_negative_auto_accept_counts_false_accept() -> None:
    row = enrich_prediction({
        "sample_id": "negative_auto_accepted",
        "expected_action": "reject",
        "ground_truth_smiles": "",
        "predicted_smiles": "CCN",
        "recognition_success": True,
        "recognition_decision": "accepted",
        "manual_review_recommended": False,
        "backend": "fake",
        "preprocessing_strategy": "original",
    }, 0.95)
    metrics = compute_metrics([row])["overall"]
    assert metrics["negative_hallucination_count"] == 1
    assert metrics["false_accept_count"] == 1
    assert metrics["false_accept_rate"] == 1.0


def test_all_negative_dataset_marks_positive_metrics_not_applicable() -> None:
    rows = [
        enrich_prediction({
            "sample_id": "negative_rejected",
            "expected_action": "reject",
            "ground_truth_smiles": "",
            "predicted_smiles": None,
            "recognition_success": False,
            "recognition_decision": "rejected",
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95)
    ]
    metrics = compute_metrics(rows)["overall"]
    assert metrics["positive_sample_count"] == 0
    assert metrics["valid_smiles_rate"] is None
    assert metrics["canonical_exact_match_rate"] is None
    assert metrics["molecule_equivalent_rate"] is None
    assert metrics["similarity_above_threshold_rate"] is None
    assert metrics["rejection_coverage"] == 1.0


def test_all_positive_dataset_marks_rejection_metrics_not_applicable() -> None:
    rows = [
        enrich_prediction({
            "sample_id": "positive_correct",
            "expected_action": "recognize",
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "recognition_decision": "accepted",
            "manual_review_recommended": False,
            "backend": "fake",
            "preprocessing_strategy": "original",
        }, 0.95)
    ]
    metrics = compute_metrics(rows)["overall"]
    assert metrics["negative_sample_count"] == 0
    assert metrics["valid_smiles_rate"] == 1.0
    assert metrics["canonical_exact_match_rate"] == 1.0
    assert metrics["rejection_coverage"] is None
    assert metrics["negative_hallucination_rate"] is None
    assert metrics["false_accept_rate"] is None


def test_run_directories_do_not_overwrite_and_report_bundle(tmp_path: Path) -> None:
    first = create_run_directory(tmp_path, "demo", timestamp="20260711_153000")
    second = create_run_directory(tmp_path, "demo", timestamp="20260711_153000")
    assert first != second
    rows = [
        enrich_prediction({
            "sample_id": "ethanol",
            "image_path": "ethanol.png",
            "ground_truth_smiles": "CCO",
            "predicted_smiles": "CCO",
            "recognition_success": True,
            "recognition_status": "success",
            "category": "clean",
            "source": "unit",
            "backend": "demo",
            "preprocessing_strategy": "original",
            "inference_time_ms": 1.0,
        }, 0.95)
    ]
    result = {
        "rows": rows,
        "metrics": compute_metrics(rows),
        "metadata": {
            "run_started_at": "2026-07-11T15:30:00+08:00",
            "git_commit": "abc",
            "python_version": "3.10",
            "rdkit_version": "test",
            "backend": "demo",
            "backend_status": {"model_name": "demo"},
            "preprocessing_strategy": "original",
            "limitations": "demo only",
        },
    }
    outputs = write_report_bundle(first, result, {"backend": "demo"})
    for key in ("config", "predictions", "metrics", "report", "failure_cases", "charts"):
        assert Path(outputs[key]).exists()


def test_release_gates_and_fixed_release_bundle(monkeypatch, tmp_path: Path) -> None:
    image = _image(tmp_path / "ethanol.png")
    manifest = _real_manifest(tmp_path / "manifest.csv", image)
    monkeypatch.setitem(MoleculeRecognizer.ADAPTERS, "fake_success", FakeSuccessAdapter)

    result = run_release_acceptance(
        release_version="v-test",
        manifest=manifest,
        dataset_root=tmp_path,
        output_root=tmp_path / "releases",
        backends=["fake_success"],
        require_real_metadata=True,
    )

    release_dir = Path(result["release_dir"])
    assert result["passed"] is False
    assert (release_dir / "fake_success_metrics.json").is_file()
    assert (release_dir / "fake_success_predictions.csv").is_file()
    assert (release_dir / "errors.csv").is_file()
    assert (release_dir / "report.md").is_file()
    payload = json.loads((release_dir / "fake_success_metrics.json").read_text(encoding="utf-8"))
    assert payload["gates"]["passed"] is False
    failed_checks = {check["metric"] for check in payload["gates"]["checks"] if not check["passed"]}
    assert "positive_sample_count" in failed_checks
    assert payload["gates"]["data_sufficiency"]["not_release_qualified"] is True


def test_release_gate_flags_threshold_failure() -> None:
    gates = evaluate_release_gates({
        "overall": {
            "valid_smiles_rate": 0.90,
            "canonical_exact_match_rate": 0.75,
            "false_accept_rate": 0.10,
            "high_risk_error_count": 1,
            "high_risk_error_review_needed_rate": 0.0,
            "p95_latency_ms": 20000,
        }
    })
    assert gates["passed"] is False
    failed = {check["metric"] for check in gates["checks"] if not check["passed"]}
    assert {
        "valid_smiles_rate",
        "canonical_exact_match_rate",
        "false_accept_rate",
        "high_risk_error_review_needed_rate",
        "p95_latency_ms",
    }.issubset(failed)
    assert "positive_sample_count" in failed


def test_release_comparison_detects_regression_and_writes_report(tmp_path: Path) -> None:
    previous = tmp_path / "releases" / "v1"
    current = tmp_path / "releases" / "v2"
    previous.mkdir(parents=True)
    current.mkdir(parents=True)
    (previous / "molscribe_metrics.json").write_text(json.dumps({
        "metrics": {"overall": {"canonical_exact_match_rate": 0.90, "valid_smiles_rate": 0.98, "p95_latency_ms": 1000}}
    }), encoding="utf-8")
    (current / "molscribe_metrics.json").write_text(json.dumps({
        "metrics": {"overall": {"canonical_exact_match_rate": 0.80, "valid_smiles_rate": 0.98, "p95_latency_ms": 1200}}
    }), encoding="utf-8")

    comparison = compare_release_dirs(current, previous)
    report_path = current / "comparison.md"
    write_comparison_report(report_path, comparison)

    assert comparison["passed"] is False
    assert any(row["metric"] == "canonical_exact_match_rate" and row["regressed"] for row in comparison["comparisons"])
    assert report_path.is_file()
