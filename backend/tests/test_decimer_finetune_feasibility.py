from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from src.ocsr.decimer_finetune_feasibility import (
    FeasibilityExportConfig,
    audit_installed_decimer,
    export_feasibility_dataset,
    select_official_aware_rows,
    sha256_file,
)


def _fake_decimer(root: Path) -> Path:
    root.mkdir()
    (root / "__init__.py").write_text("from .decimer import predict_SMILES\n", encoding="utf-8")
    (root / "decimer.py").write_text(
        "import tensorflow as tf\nDECIMER_V2=tf.saved_model.load('model')\ndef predict_SMILES(path): pass\n",
        encoding="utf-8",
    )
    (root / "DECIMER_EfficinetNetV2_Transfomer_Trainer.py").write_text(
        "TPUClusterResolver()\npath='gs://bucket'\ntotal_data = 1000000\n",
        encoding="utf-8",
    )
    (root / "Predictor_usingCheckpoints.py").write_text(
        'raise ImportError("Please use tensorflow 2.10 when working with the checkpoints.")\n',
        encoding="utf-8",
    )
    return root


def _dataset(root: Path, count: int = 60) -> tuple[Path, Path]:
    dataset = root / "dataset"
    images = dataset / "images" / "official_clean"
    images.mkdir(parents=True)
    fields = [
        "sample_id", "pubchem_cid", "image_path", "image_variant", "image_sha256",
        "ground_truth_smiles", "ground_truth_canonical_smiles", "ground_truth_isomeric_smiles",
        "ground_truth_inchikey", "ground_truth_formula", "split", "ground_truth_origin", "review_status",
    ]
    rows = []
    for cid in range(1, count + 1):
        image = images / f"CID_{cid}.png"
        Image.new("RGB", (16, 16), "white").save(image)
        rows.append({
            "sample_id": f"pubchem_{cid}_official_clean", "pubchem_cid": str(cid),
            "image_path": f"images/official_clean/CID_{cid}.png", "image_variant": "official_clean",
            "image_sha256": sha256_file(image), "ground_truth_smiles": "CCO",
            "ground_truth_canonical_smiles": "CCO", "ground_truth_isomeric_smiles": "CCO",
            "ground_truth_inchikey": f"KEY-{cid}", "ground_truth_formula": "C2H6O", "split": "train",
            "ground_truth_origin": "pubchem", "review_status": "source_verified",
        })
    manifest = dataset / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    return dataset, manifest


def test_installed_style_package_is_a_hard_stop(tmp_path: Path) -> None:
    audit = audit_installed_decimer(_fake_decimer(tmp_path / "DECIMER"), "2.21.0")
    assert audit["research_trainer_present"] is True
    assert audit["public_training_interface"] is False
    assert audit["can_resume_existing_weights"] is False
    assert audit["hard_stop"] is True


def test_selects_exactly_50_train_official_cids_deterministically(tmp_path: Path) -> None:
    _, manifest = _dataset(tmp_path)
    config = FeasibilityExportConfig()
    first = select_official_aware_rows(manifest, config)
    second = select_official_aware_rows(manifest, config)
    assert [row["pubchem_cid"] for row in first] == [row["pubchem_cid"] for row in second]
    assert len({row["pubchem_cid"] for row in first}) == 50
    assert all(row["split"] == "train" and row["image_variant"] == "official_clean" for row in first)
    assert sum(row["probe_split"] == "probe_train" for row in first) == 40
    assert sum(row["probe_split"] == "probe_dev" for row in first) == 10


def test_blocked_export_preserves_source_and_refuses_overwrite(tmp_path: Path) -> None:
    dataset, manifest = _dataset(tmp_path)
    before = hashlib.sha256(manifest.read_bytes()).hexdigest()
    audit = audit_installed_decimer(_fake_decimer(tmp_path / "DECIMER"), "2.21.0")
    output = tmp_path / "output"
    report = export_feasibility_dataset(manifest, dataset, output, audit)
    assert report["status"] == "blocked_before_training"
    assert report["training_started"] is False
    assert report["measurements"]["single_batch_peak_gpu_memory_mib"] is None
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == before
    assert len(list((output / "images" / "official_clean").glob("*.png"))) == 50
    with pytest.raises(FileExistsError):
        export_feasibility_dataset(manifest, dataset, output, audit)
