"""Prepare a bounded official-code MolScribe fine-tuning probe."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rdkit import Chem

from src.ocsr.decimer_finetune_feasibility import (
    FeasibilityExportConfig,
    select_official_aware_rows,
    sha256_file,
)


OFFICIAL_REPOSITORY = "https://github.com/thomas0809/MolScribe"
OFFICIAL_COMMIT = "7296a30413eb55436702011efdff78131f66d162"
PROTOCOL_VERSION = "molscribe-official-aware-probe-v0.1"


@dataclass(frozen=True)
class MolScribeProbeConfig:
    cid_count: int = 50
    train_cids: int = 40
    dev_cids: int = 10
    maximum_steps: int = 100
    repeated_train_rows: int = 120
    batch_size: int = 1
    mixed_precision: bool = True
    maximum_runtime_seconds: int = 7200
    memory_stop_mib: int = 15 * 1024
    seed: int = 20260718
    input_size: int = 384
    coord_bins: int = 128
    formats: tuple[str, ...] = ("chartok_coords", "edges")

    def validate(self) -> None:
        if (self.cid_count, self.train_cids, self.dev_cids) != (50, 40, 10):
            raise ValueError("Probe identity split is fixed to 40 train / 10 dev from 50 CIDs.")
        if self.maximum_steps > 100 or self.batch_size != 1 or not self.mixed_precision:
            raise ValueError("Probe is limited to <=100 steps, batch size 1 and mixed precision.")
        if self.maximum_runtime_seconds > 7200:
            raise ValueError("Probe runtime may not exceed two hours.")
        if self.repeated_train_rows <= self.maximum_steps:
            raise ValueError("Training CSV must remain longer than the bounded partial epoch.")


def _edge_type(bond: Chem.Bond) -> int:
    if bond.GetIsAromatic():
        return 4
    value = float(bond.GetBondTypeAsDouble())
    return {1.0: 1, 2.0: 2, 3.0: 3}.get(value, 1)


def smiles_edges(smiles: str) -> list[list[int]]:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"RDKit cannot parse trusted SMILES: {smiles}")
    return [
        [bond.GetBeginAtomIdx(), bond.GetEndAtomIdx(), _edge_type(bond)]
        for bond in molecule.GetBonds()
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare_molscribe_probe(
    source_dataset: Path,
    output_dir: Path,
    checkpoint_path: Path,
    official_source_dir: Path,
    config: MolScribeProbeConfig | None = None,
) -> dict[str, Any]:
    config = config or MolScribeProbeConfig()
    config.validate()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite MolScribe probe: {output_dir}")
    if not checkpoint_path.is_file():
        raise FileNotFoundError(checkpoint_path)
    if not (official_source_dir / "train.py").is_file():
        raise FileNotFoundError("Official MolScribe train.py is missing")

    source_manifest = source_dataset / "manifest.csv"
    source_hash_before = sha256_file(source_manifest)
    selection = select_official_aware_rows(
        source_manifest,
        FeasibilityExportConfig(seed=config.seed),
    )
    staging = output_dir.with_name(output_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    images_dir = staging / "images"
    images_dir.mkdir(parents=True)

    converted: list[dict[str, Any]] = []
    for row in selection:
        source_image = source_dataset / row["image_path"]
        if sha256_file(source_image) != row["image_sha256"]:
            raise ValueError(f"Source image hash mismatch for CID {row['pubchem_cid']}")
        target_rel = Path("images") / f"CID_{row['pubchem_cid']}.png"
        shutil.copy2(source_image, staging / target_rel)
        smiles = row["ground_truth_isomeric_smiles"]
        converted.append({
            "image_id": row["sample_id"],
            "pubchem_cid": row["pubchem_cid"],
            "file_path": target_rel.as_posix(),
            "SMILES": smiles,
            "edges": repr(smiles_edges(smiles)),
            "source_image_sha256": row["image_sha256"],
            "ground_truth_origin": "pubchem",
            "source_split": "train",
        })

    unique_train = converted[: config.train_cids]
    dev = converted[config.train_cids :]
    repeated_train: list[dict[str, Any]] = []
    for step_index in range(config.repeated_train_rows):
        row = unique_train[step_index % len(unique_train)].copy()
        row["probe_occurrence"] = step_index
        repeated_train.append(row)
    unique_train = [dict(row, probe_occurrence=0) for row in unique_train]
    dev = [dict(row, probe_occurrence=0) for row in dev]
    _write_csv(staging / "train_unique.csv", unique_train)
    _write_csv(staging / "train_probe.csv", repeated_train)
    _write_csv(staging / "valid.csv", dev)

    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "official_repository": OFFICIAL_REPOSITORY,
        "official_commit": OFFICIAL_COMMIT,
        "official_source_dir": str(official_source_dir),
        "checkpoint_path": str(checkpoint_path.resolve()),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "checkpoint_overwrite_allowed": False,
        "source_manifest_sha256": source_hash_before,
        "source_dataset": "ocsr-trusted-v0.1 train / official_clean",
        "coordinate_supervision": "masked because image-aligned atom coordinates are unavailable",
        "edge_supervision": "derived deterministically from PubChem isomeric SMILES with RDKit",
        "model_predictions_used_as_ground_truth": False,
        "v0_3_built": False,
    }
    (staging / "protocol.json").write_text(json.dumps(protocol, indent=2, sort_keys=True), encoding="utf-8")
    if sha256_file(source_manifest) != source_hash_before:
        raise RuntimeError("v0.1 source manifest changed during probe preparation")
    staging.replace(output_dir)
    return protocol
