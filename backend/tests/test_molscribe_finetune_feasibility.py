from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PIL import Image

from src.ocsr.decimer_finetune_feasibility import sha256_file
from src.ocsr.molscribe_finetune_feasibility import (
    MolScribeProbeConfig,
    prepare_molscribe_probe,
    smiles_edges,
)


def _source_dataset(root: Path) -> Path:
    dataset = root / "source"
    images = dataset / "images" / "official_clean"
    images.mkdir(parents=True)
    rows = []
    for cid in range(1, 61):
        image = images / f"CID_{cid}.png"
        Image.new("RGB", (24, 24), "white").save(image)
        rows.append({
            "sample_id": f"pubchem_{cid}_official_clean",
            "pubchem_cid": str(cid),
            "image_path": f"images/official_clean/CID_{cid}.png",
            "image_variant": "official_clean",
            "image_sha256": sha256_file(image),
            "ground_truth_isomeric_smiles": "CCO",
            "split": "train",
            "ground_truth_origin": "pubchem",
            "review_status": "source_verified",
        })
    with (dataset / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return dataset


def test_smiles_edges_are_official_graph_head_compatible() -> None:
    assert smiles_edges("CC=O") == [[0, 1, 1], [1, 2, 2]]


def test_probe_export_is_bounded_and_does_not_overwrite(tmp_path: Path) -> None:
    source = _source_dataset(tmp_path)
    checkpoint = tmp_path / "model.pth"; checkpoint.write_bytes(b"checkpoint")
    official = tmp_path / "official"; official.mkdir(); (official / "train.py").write_text("# official")
    output = tmp_path / "probe"
    protocol = prepare_molscribe_probe(source, output, checkpoint, official)
    train = list(csv.DictReader((output / "train_probe.csv").open(encoding="utf-8")))
    valid = list(csv.DictReader((output / "valid.csv").open(encoding="utf-8")))
    assert len(train) == 120 and len(valid) == 10
    assert len({row["pubchem_cid"] for row in train}) == 40
    assert protocol["checkpoint_overwrite_allowed"] is False
    assert protocol["model_predictions_used_as_ground_truth"] is False
    with pytest.raises(FileExistsError):
        prepare_molscribe_probe(source, output, checkpoint, official)


def test_probe_limits_are_frozen() -> None:
    with pytest.raises(ValueError):
        MolScribeProbeConfig(maximum_steps=101).validate()
