"""Conservative DECIMER fine-tuning feasibility audit and data export.

This module deliberately stops before training when the installed DECIMER
distribution does not expose a supported continuation-training interface.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "decimer-official-aware-feasibility-v0.1"
FORBIDDEN_EXPORT_FIELDS = {
    "molscribe_smiles",
    "decimer_smiles",
    "ensemble_smiles",
    "prediction",
    "predicted_smiles",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FeasibilityExportConfig:
    cid_count: int = 50
    probe_train_count: int = 40
    seed: int = 20260718
    source_split: str = "train"
    image_variant: str = "official_clean"
    epochs: int = 1
    batch_size: int = 1
    mixed_precision: bool = True
    maximum_runtime_hours: int = 2
    memory_stop_mib: int = 15 * 1024

    def validate(self) -> None:
        if self.cid_count != 50:
            raise ValueError("This feasibility protocol is frozen to exactly 50 CIDs.")
        if not 0 < self.probe_train_count < self.cid_count:
            raise ValueError("probe_train_count must leave a non-empty probe_dev split.")
        if self.epochs != 1 or self.batch_size != 1 or not self.mixed_precision:
            raise ValueError("The probe requires one epoch, batch size 1, and mixed precision.")
        if self.maximum_runtime_hours > 2:
            raise ValueError("The feasibility probe may not run longer than two hours.")
        if self.source_split != "train" or self.image_variant != "official_clean":
            raise ValueError("Only v0.1 train official_clean samples are allowed.")


def _version_tuple(value: str) -> tuple[int, int]:
    match = re.match(r"\s*(\d+)\.(\d+)", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else (999, 999)


def audit_installed_decimer(package_root: Path, tensorflow_version: str) -> dict[str, Any]:
    """Inspect package source without importing DECIMER or initializing CUDA."""

    init_path = package_root / "__init__.py"
    trainer_path = package_root / "DECIMER_EfficinetNetV2_Transfomer_Trainer.py"
    checkpoint_path = package_root / "Predictor_usingCheckpoints.py"
    decimer_path = package_root / "decimer.py"
    required = (init_path, decimer_path)
    if not all(path.is_file() for path in required):
        return {
            "package_found": False,
            "public_training_interface": False,
            "can_resume_existing_weights": False,
            "hard_stop": True,
            "stop_reasons": ["DECIMER package source is incomplete or unavailable"],
        }

    init_source = init_path.read_text(encoding="utf-8", errors="replace")
    inference_source = decimer_path.read_text(encoding="utf-8", errors="replace")
    trainer_source = trainer_path.read_text(encoding="utf-8", errors="replace") if trainer_path.is_file() else ""
    checkpoint_source = checkpoint_path.read_text(encoding="utf-8", errors="replace") if checkpoint_path.is_file() else ""

    public_train = bool(re.search(r"(?:from\s+.+\s+import\s+train|def\s+train\w*)", init_source))
    research_trainer_present = bool(trainer_source)
    trainer_is_tpu_hardcoded = all(
        marker in trainer_source
        for marker in ("TPUClusterResolver", "gs://", "total_data = 1000000")
    )
    saved_model_inference_only = "tf.saved_model.load" in inference_source and "predict_SMILES" in inference_source
    checkpoint_requires_tf210 = "Please use tensorflow 2.10" in checkpoint_source
    tensorflow_compatible = _version_tuple(tensorflow_version) <= (2, 10)

    can_resume = bool(
        public_train
        and not trainer_is_tpu_hardcoded
        and checkpoint_path.is_file()
        and (not checkpoint_requires_tf210 or tensorflow_compatible)
        and not saved_model_inference_only
    )
    reasons: list[str] = []
    if not public_train:
        reasons.append("installed DECIMER package exposes inference only; no public training API")
    if research_trainer_present and trainer_is_tpu_hardcoded:
        reasons.append("bundled research trainer is hard-coded for TPU, GCS and one million samples")
    if saved_model_inference_only:
        reasons.append("production weights are loaded as an inference SavedModel without a supported optimizer/training contract")
    if checkpoint_requires_tf210 and not tensorflow_compatible:
        reasons.append(
            f"checkpoint reconstruction requires TensorFlow <=2.10, but installed TensorFlow is {tensorflow_version}"
        )
    if not can_resume:
        reasons.append("existing weights cannot be reliably resumed and round-tripped in the current environment")

    return {
        "package_found": True,
        "package_root": str(package_root),
        "tensorflow_version": tensorflow_version,
        "public_training_interface": public_train,
        "research_trainer_present": research_trainer_present,
        "research_trainer_supported": research_trainer_present and not trainer_is_tpu_hardcoded,
        "trainer_is_tpu_gcs_hardcoded": trainer_is_tpu_hardcoded,
        "inference_saved_model": saved_model_inference_only,
        "checkpoint_requires_tensorflow_2_10_or_older": checkpoint_requires_tf210,
        "tensorflow_checkpoint_compatible": tensorflow_compatible,
        "can_resume_existing_weights": can_resume,
        "hard_stop": not public_train or not can_resume,
        "stop_reasons": reasons,
    }


def select_official_aware_rows(
    manifest_path: Path,
    config: FeasibilityExportConfig,
) -> list[dict[str, str]]:
    config.validate()
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    eligible = [
        row
        for row in rows
        if row.get("split") == config.source_split
        and row.get("image_variant") == config.image_variant
        and row.get("ground_truth_origin") == "pubchem"
        and row.get("review_status") == "source_verified"
    ]
    by_cid: dict[str, dict[str, str]] = {}
    for row in eligible:
        by_cid.setdefault(row["pubchem_cid"], row)
    if len(by_cid) < config.cid_count:
        raise ValueError(f"Need {config.cid_count} eligible CIDs, found {len(by_cid)}")

    def selection_key(item: tuple[str, dict[str, str]]) -> str:
        return hashlib.sha256(f"{config.seed}:{item[0]}".encode()).hexdigest()

    selected = [row.copy() for _, row in sorted(by_cid.items(), key=selection_key)[: config.cid_count]]
    for index, row in enumerate(selected):
        row["probe_split"] = "probe_train" if index < config.probe_train_count else "probe_dev"
        row["feasibility_protocol"] = PROTOCOL_VERSION
    return selected


def export_feasibility_dataset(
    manifest_path: Path,
    dataset_root: Path,
    output_dir: Path,
    package_audit: dict[str, Any],
    config: FeasibilityExportConfig | None = None,
) -> dict[str, Any]:
    config = config or FeasibilityExportConfig()
    config.validate()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite feasibility export: {output_dir}")

    source_manifest_hash_before = sha256_file(manifest_path)
    rows = select_official_aware_rows(manifest_path, config)
    staging = output_dir.with_name(output_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    image_dir = staging / "images" / "official_clean"
    image_dir.mkdir(parents=True)

    exported_rows: list[dict[str, str]] = []
    for row in rows:
        if FORBIDDEN_EXPORT_FIELDS.intersection(row):
            raise ValueError("Prediction fields are forbidden in the feasibility training export.")
        source = dataset_root / row["image_path"]
        if not source.is_file() or sha256_file(source) != row["image_sha256"]:
            raise ValueError(f"Missing or modified source image for CID {row['pubchem_cid']}")
        relative = Path("images") / "official_clean" / f"CID_{row['pubchem_cid']}{source.suffix.lower()}"
        destination = staging / relative
        shutil.copy2(source, destination)
        exported = row.copy()
        exported["image_path"] = relative.as_posix()
        exported["source_dataset"] = "ocsr-trusted-v0.1"
        exported_rows.append(exported)

    fieldnames = list(exported_rows[0])
    with (staging / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(exported_rows)

    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "selection": "SHA-256(seed:CID), first 50 eligible unique CIDs",
        "allowed_source": "ocsr-trusted-v0.1 train / official_clean only",
        "official_aware": True,
        "ground_truth_origin": "pubchem",
        "model_predictions_used_as_ground_truth": False,
        "training_started": False,
        "v0_3_built": False,
        "source_manifest_sha256": source_manifest_hash_before,
    }
    (staging / "protocol.json").write_text(json.dumps(protocol, indent=2, sort_keys=True), encoding="utf-8")

    hard_stop = bool(package_audit.get("hard_stop"))
    report = {
        "status": "blocked_before_training" if hard_stop else "ready_for_single_batch_probe",
        "selected_cids": len(exported_rows),
        "probe_train_cids": sum(row["probe_split"] == "probe_train" for row in exported_rows),
        "probe_dev_cids": sum(row["probe_split"] == "probe_dev" for row in exported_rows),
        "image_variant": "official_clean",
        "package_audit": package_audit,
        "measurements": {
            "single_batch_peak_gpu_memory_mib": None,
            "oom": None,
            "estimated_seconds_per_100_images": None,
            "checkpoint_save_reload": None,
            "one_epoch_dev_metrics": None,
        },
        "training_started": False,
        "stop_reasons": package_audit.get("stop_reasons", []) if hard_stop else [],
    }
    (staging / "feasibility_report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    reason_lines = "\n".join(f"- {reason}" for reason in report["stop_reasons"]) or "- No hard stop detected."
    markdown = f"# DECIMER fine-tuning feasibility\n\nStatus: **{report['status']}**\n\n"
    markdown += f"Exported {len(exported_rows)} unique v0.1 train CIDs using official_clean only "
    markdown += f"({report['probe_train_cids']} probe_train / {report['probe_dev_cids']} probe_dev).\n\n"
    markdown += "## Stop reasons\n\n" + reason_lines + "\n\n"
    markdown += "No training, v0.3 construction, MolScribe, ensemble, or external evaluation was run.\n"
    (staging / "feasibility_report.md").write_text(markdown, encoding="utf-8")

    checksums = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        checksums.append(f"{sha256_file(path)}  {path.relative_to(staging).as_posix()}")
    (staging / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    if sha256_file(manifest_path) != source_manifest_hash_before:
        raise RuntimeError("Source v0.1 manifest changed during export")
    staging.replace(output_dir)
    return report
