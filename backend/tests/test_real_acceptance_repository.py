from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from PIL import Image

from scripts.download_real_acceptance_set import DATASET_ROOT, SOURCE_MANIFEST, download_acceptance_set
from scripts.validate_real_acceptance_set import validate_real_acceptance_set
from src.evaluation.dataset import ManifestValidationError, load_manifest
from src.evaluation.metrics import compute_metrics, enrich_prediction
from src.evaluation.release_gate import evaluate_release_gates


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DATASET_ROOT = PROJECT_ROOT / "data" / "ocsr_real_acceptance"
REAL_MANIFEST = REAL_DATASET_ROOT / "manifest.csv"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _materialized_repository_rows() -> list[dict[str, str]]:
    result = download_acceptance_set(SOURCE_MANIFEST)
    assert result["failed"] == 0
    validation = validate_real_acceptance_set()
    assert validation["passed"] is True
    return _read_rows(REAL_MANIFEST)


def _perfect_prediction_rows(samples) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in samples:
        positive = sample.expected_action != "reject"
        rows.append(
            enrich_prediction(
                {
                    "sample_id": sample.sample_id,
                    "image_path": str(sample.image_path),
                    "image_sha256": sample.image_sha256,
                    "ground_truth_smiles": sample.ground_truth_smiles,
                    "ground_truth_inchikey_manifest": sample.ground_truth_inchikey,
                    "predicted_smiles": sample.ground_truth_smiles if positive else None,
                    "expected_action": sample.expected_action,
                    "recognition_success": positive,
                    "recognition_decision": "accepted" if positive else "rejected",
                    "manual_review_recommended": not positive,
                    "source_document": sample.source_document,
                    "source_license": sample.source_license,
                    "review_status": sample.review_status,
                    "scaffold_key": sample.scaffold_key,
                    "perturbation": sample.perturbation,
                    "category": sample.category,
                    "source": sample.source,
                    "image_quality": sample.image_quality,
                    "complexity": sample.complexity,
                    "structure_features": sample.structure_features,
                    "backend": "perfect_fake",
                    "preprocessing_strategy": "original",
                },
                0.95,
            )
        )
    return rows


def test_repository_real_acceptance_download_validate_and_hashes() -> None:
    rows = _materialized_repository_rows()
    samples = load_manifest(REAL_MANIFEST, REAL_DATASET_ROOT, require_real_metadata=True)

    assert len(samples) == len(rows) > 0
    assert any(sample.expected_action == "recognize" for sample in samples)
    assert any(sample.expected_action == "reject" for sample in samples)
    for sample in samples:
        assert sample.image_path.is_file()
        assert _sha256(sample.image_path) == sample.image_sha256
        assert str(sample.image_path.resolve()).startswith(str(REAL_DATASET_ROOT.resolve()))


def test_repository_real_acceptance_metrics_keep_perturbations_out_of_independent_source_counts() -> None:
    _materialized_repository_rows()
    samples = load_manifest(REAL_MANIFEST, REAL_DATASET_ROOT, require_real_metadata=True)
    metrics = compute_metrics(_perfect_prediction_rows(samples))["overall"]

    source_documents = {sample.source_document for sample in samples}
    original_source_documents = {
        sample.source_document
        for sample in samples
        if sample.perturbation.strip().lower() in {"", "none", "unspecified"}
    }
    assert metrics["total_samples"] == len(samples)
    assert metrics["derived_perturbation_count"] > 0
    assert metrics["independent_source_document_count"] == len(source_documents)
    assert metrics["independent_original_image_count"] == len(original_source_documents)
    assert metrics["independent_original_image_count"] < metrics["total_samples"]


def test_repository_starter_dataset_fails_release_gate_because_it_is_not_sufficient() -> None:
    _materialized_repository_rows()
    samples = load_manifest(REAL_MANIFEST, REAL_DATASET_ROOT, require_real_metadata=True)
    metrics = compute_metrics(_perfect_prediction_rows(samples))["overall"]
    gates = evaluate_release_gates({"overall": metrics})
    failed = {check["metric"] for check in gates["checks"] if not check["passed"]}

    assert gates["passed"] is False
    assert gates["data_sufficiency"]["starter_dataset_only"] is True
    assert gates["data_sufficiency"]["not_statistically_meaningful"] is True
    assert gates["data_sufficiency"]["not_release_qualified"] is True
    assert "positive_sample_count" in failed
    assert "negative_sample_count" in failed
    assert "independent_source_document_count" in failed
    assert "unique_molecule_count" in failed
    assert "unique_scaffold_count" in failed


def test_release_gate_blocks_missing_images_checksum_errors_unverified_and_unclear_licenses() -> None:
    release_sized_metrics = {
        "valid_smiles_rate": 1.0,
        "canonical_exact_match_rate": 1.0,
        "false_accept_rate": 0.0,
        "high_risk_error_count": 0,
        "p95_latency_ms": 10.0,
        "positive_sample_count": 100,
        "negative_sample_count": 20,
        "recognition_metric_denominator": 100,
        "rejection_metric_denominator": 20,
        "independent_source_document_count": 30,
        "unique_molecule_count": 100,
        "unique_scaffold_count": 50,
        "verified_sample_rate": 1.0,
        "license_unclear_count": 0,
        "missing_image_count": 0,
        "checksum_error_count": 0,
    }
    cases = {
        "missing_image_count": 1,
        "checksum_error_count": 1,
        "verified_sample_rate": 0.99,
        "license_unclear_count": 1,
    }
    for metric, bad_value in cases.items():
        payload = dict(release_sized_metrics)
        payload[metric] = bad_value
        gates = evaluate_release_gates({"overall": payload})
        failed = {check["metric"] for check in gates["checks"] if not check["passed"]}
        assert gates["passed"] is False
        assert metric in failed


def test_real_acceptance_manifest_rejects_unverified_rows(tmp_path: Path) -> None:
    rows = _materialized_repository_rows()
    row = dict(rows[0])
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source_image = REAL_DATASET_ROOT / row["image_path"]
    image = image_dir / Path(row["image_path"]).name
    image.write_bytes(source_image.read_bytes())
    row["image_path"] = f"images/{image.name}"
    row["image_sha256"] = _sha256(image)
    row["review_status"] = "pending"
    manifest = _write_rows(tmp_path / "manifest.csv", [row])

    try:
        load_manifest(manifest, tmp_path, require_real_metadata=True)
    except ManifestValidationError as exc:
        assert "review_status must be 'verified'" in str(exc)
    else:
        raise AssertionError("Expected unverified real acceptance row to be rejected.")


def test_real_acceptance_manifest_rejects_license_unclear_public_release_rows(tmp_path: Path) -> None:
    image = tmp_path / "ethanol.png"
    Image.new("RGB", (16, 16), "white").save(image)
    row = {
        "sample_id": "license_unclear",
        "image_path": image.name,
        "dataset_version": "v-test",
        "image_sha256": _sha256(image),
        "ground_truth_smiles": "CCO",
        "ground_truth_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "expected_action": "recognize",
        "supported_scope": "single_molecule",
        "category": "clear_single_molecule",
        "source": "unit",
        "source_document": "unit-doc",
        "source_license": "No explicit repository license found",
        "annotator": "unit-annotator",
        "reviewer": "unit-reviewer",
        "review_status": "verified",
        "split": "test",
        "scaffold_key": "alcohol",
        "image_quality": "clean",
        "complexity": "low",
        "perturbation": "none",
        "structure_features": "alcohol",
        "notes": "license gate test",
    }
    manifest = _write_rows(tmp_path / "manifest.csv", [row])
    sample = load_manifest(manifest, tmp_path, require_real_metadata=True)[0]
    metrics = compute_metrics(_perfect_prediction_rows([sample]))["overall"]
    gates = evaluate_release_gates({"overall": {**metrics, "positive_sample_count": 100, "negative_sample_count": 20}})
    failed = {check["metric"] for check in gates["checks"] if not check["passed"]}

    assert metrics["license_unclear_count"] == 1
    assert "license_unclear_count" in failed
