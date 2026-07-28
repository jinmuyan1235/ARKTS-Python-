from __future__ import annotations

import csv
import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from src.datasets.trusted_ocsr import (
    MANIFEST_FIELDS, SOURCE_FIELDS, TrustedDatasetBuildConfig, TrustedOCSRDatasetBuilder,
    assign_grouped_splits, sha256_file, validate_trusted_dataset,
)
from src.evaluation.trusted_ocsr import evaluate_prediction, evaluate_trusted_manifest, summarize_predictions
from src.ocsr.base import OCSRResult


def _png_bytes() -> bytes:
    buffer = BytesIO(); Image.new("RGB", (64, 64), "white").save(buffer, "PNG"); return buffer.getvalue()


class FakePubChemClient:
    def __init__(self, structures: dict[int, str]) -> None:
        self.structures = structures

    def get_bytes(self, url: str, **_kwargs):
        if "/property/" in url:
            cid_text = url.split("/cid/", 1)[1].split("/property/", 1)[0]
            rows = []
            for cid in map(int, cid_text.split(",")):
                if cid not in self.structures: continue
                mol = Chem.MolFromSmiles(self.structures[cid])
                rows.append({
                    "CID": cid, "SMILES": Chem.MolToSmiles(mol, isomericSmiles=True),
                    "ConnectivitySMILES": Chem.MolToSmiles(mol, isomericSmiles=False),
                    "InChIKey": Chem.MolToInchiKey(mol), "MolecularFormula": rdMolDescriptors.CalcMolFormula(mol),
                    "MolecularWeight": 1,
                })
            payload = json.dumps({"PropertyTable": {"Properties": rows}}).encode()
        else:
            payload = _png_bytes()
        return payload, {"sha256": hashlib.sha256(payload).hexdigest()}


def _write_csv(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def _trusted_fixture(root: Path, smiles: str = "F[C@H](Cl)Br", origin: str = "pubchem") -> Path:
    (root / "images/official_clean").mkdir(parents=True)
    (root / "images/rendered_clean").mkdir(parents=True)
    (root / "images/perturbations").mkdir(parents=True)
    (root / "metadata").mkdir()
    mol = Chem.MolFromSmiles(smiles)
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)
    isomeric = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    inchikey = Chem.MolToInchiKey(mol)
    rows = []
    for variant, rel in (
        ("official_clean", "images/official_clean/CID_1.png"),
        ("rendered_clean", "images/rendered_clean/CID_1.png"),
        ("synthetic_perturbation", "images/perturbations/CID_1.png"),
    ):
        path = root / rel; Image.new("RGB", (64, 64), "white").save(path)
        rows.append({
            "sample_id": f"pubchem_1_{variant}", "pubchem_cid": "1", "image_path": rel,
            "image_variant": variant, "image_sha256": sha256_file(path), "ground_truth_smiles": isomeric,
            "ground_truth_canonical_smiles": canonical, "ground_truth_isomeric_smiles": isomeric,
            "ground_truth_inchikey": inchikey, "ground_truth_formula": rdMolDescriptors.CalcMolFormula(mol),
            "expected_action": "recognize", "source": "PubChem", "source_url": "https://example.invalid/1",
            "source_license": "NCBI molecular-data usage policy", "downloaded_at": "2026-07-18T00:00:00Z",
            "dataset_version": "ocsr-trusted-v0.1", "split": "test", "scaffold_key": f"inchikey:{inchikey}",
            "structure_features": "stereochemical;halogen", "perturbation": "deterministic_composite" if "perturbation" in variant else "none",
            "perturbation_parameters": json.dumps({"seed": 7}) if "perturbation" in variant else "{}",
            "ground_truth_origin": origin, "review_status": "source_verified", "atom_count": mol.GetNumAtoms(),
            "heavy_atom_count": mol.GetNumHeavyAtoms(), "molecular_weight": 100, "ring_count": 0,
        })
    _write_csv(root / "manifest.csv", MANIFEST_FIELDS, rows)
    _write_csv(root / "source_manifest.csv", SOURCE_FIELDS, [{
        "pubchem_cid": "1", "property_url": "https://example.invalid/property", "image_url": "https://example.invalid/image",
        "property_response_sha256": "a" * 64, "image_response_sha256": "b" * 64,
        "metadata_path": "metadata/CID_1.json", "downloaded_at": "2026-07-18T00:00:00Z",
        "source_license": "NCBI molecular-data usage policy", "source_policy_url": "https://example.invalid/policy",
        "ground_truth_origin": "pubchem",
    }])
    source_path = root / "source_manifest.csv"
    source_rows = list(csv.DictReader(source_path.open("r", encoding="utf-8", newline="")))
    source_rows[0]["property_url"] = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/property/x/JSON"
    source_rows[0]["image_url"] = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG"
    _write_csv(source_path, SOURCE_FIELDS, source_rows)
    (root / "metadata/CID_1.json").write_text(json.dumps({
        "cid": 1, "property_response_sha256": "a" * 64, "image_response_sha256": "b" * 64,
    }), encoding="utf-8")
    (root / "dataset_summary.json").write_text(json.dumps({"successful_cids": 1}), encoding="utf-8")
    (root / "protocol.json").write_text(json.dumps({"test_usage": "evaluation only"}), encoding="utf-8")
    lines = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "checksums.sha256"):
        lines.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root / "manifest.csv"


def test_pubchem_cid_images_and_structure_are_bound_and_snapshot_cannot_overwrite(tmp_path: Path):
    cid_file = tmp_path / "cids.txt"; cid_file.write_text("1 2", encoding="utf-8")
    output = tmp_path / "snapshot"
    config = TrustedDatasetBuildConfig(output, tmp_path / "cache", 2, 2, 2, 11, cid_file)
    builder = TrustedOCSRDatasetBuilder(config, FakePubChemClient({1: "CCO", 2: "c1ccccc1"}))
    summary = builder.build()
    assert summary["successful_cids"] == 2
    validation = validate_trusted_dataset(output)
    assert validation["valid"], validation["errors"]
    with pytest.raises(FileExistsError): builder.build()


def test_split_groups_cid_inchikey_and_scaffold_together():
    records = [
        {"cid": 1, "scaffold_key": "A"}, {"cid": 2, "scaffold_key": "A"},
        {"cid": 3, "scaffold_key": "B"}, {"cid": 4, "scaffold_key": "C"},
    ]
    splits = assign_grouped_splits(records, 7)
    assert splits[1] == splits[2]


def test_formula_inchikey_and_hash_tampering_fail(tmp_path: Path):
    manifest = _trusted_fixture(tmp_path / "dataset")
    assert validate_trusted_dataset(manifest.parent)["valid"]
    tampered = manifest.parent / "images/official_clean/CID_1.png"
    tampered.write_bytes(tampered.read_bytes() + b"tampered")
    assert not validate_trusted_dataset(manifest.parent)["valid"]


def test_prediction_cannot_be_ground_truth(tmp_path: Path):
    manifest = _trusted_fixture(tmp_path / "dataset", origin="model_prediction")
    with pytest.raises(ValueError, match="validation failed"):
        evaluate_trusted_manifest(manifest, "molscribe", tmp_path / "out", predictor=lambda _p: OCSRResult("CC", None, "molscribe", "success", "ok"))


def test_exact_identity_distinguishes_stereo_from_connectivity():
    row = {
        "ground_truth_isomeric_smiles": "F[C@H](Cl)Br", "ground_truth_canonical_smiles": "FC(Cl)Br",
        "ground_truth_inchikey": Chem.MolToInchiKey(Chem.MolFromSmiles("F[C@H](Cl)Br")),
    }
    result = OCSRResult("F[C@@H](Cl)Br", None, "molscribe", "success", "ok")
    evaluated = evaluate_prediction(row, result, 2.0)
    assert evaluated["connectivity_match"] is True
    assert evaluated["stereochemistry_exact_match"] is False
    assert evaluated["inchikey_exact_match"] is False
    assert evaluated["error_type"] == "stereochemistry_error"


def test_empty_prediction_is_not_a_valid_smiles():
    row = {"ground_truth_isomeric_smiles": "CCO"}
    result = OCSRResult(None, None, "molscribe", "failed", "no output")
    evaluated = evaluate_prediction(row, result, 1.0)
    assert evaluated["valid_smiles"] is False
    assert evaluated["canonical_exact_match"] is False


def test_ensemble_overlap_and_abstention_statistics():
    truth_smiles = "CCO"
    truth = Chem.MolToInchiKey(Chem.MolFromSmiles(truth_smiles))
    row = {"ground_truth_isomeric_smiles": truth_smiles, "ground_truth_inchikey": truth}
    result = OCSRResult(
        None, None, "ensemble", "failed", "disagreement", decision="review_needed",
        candidates=[
            {"backend": "molscribe", "raw_smiles": "CCO"},
            {"backend": "decimer", "raw_smiles": "CCC"},
        ],
    )
    evaluated = evaluate_prediction(row, result, 3.0)
    metrics = summarize_predictions([evaluated])
    assert metrics["only_molscribe_correct_count"] == 1
    assert metrics["model_disagreement_count"] == 1
    assert metrics["ensemble_unnecessary_reject_count"] == 1
    assert metrics["ensemble_abstention_count"] == 1


def test_evaluator_uses_only_test_split_and_writes_required_outputs(tmp_path: Path):
    manifest = _trusted_fixture(tmp_path / "dataset", smiles="CCO")
    output = tmp_path / "evaluation"
    result = evaluate_trusted_manifest(
        manifest, "molscribe", output,
        predictor=lambda _p: OCSRResult("CCO", 1.0, "molscribe", "success", "ok", inference_time_ms=1.0),
    )
    assert result["metrics"]["inchikey_exact_match_rate"] == 1.0
    assert result["metadata"]["test_used_for_tuning"] is False
    for name in ("metrics.json", "predictions.csv", "errors.csv", "per_variant_metrics.csv", "per_feature_metrics.csv", "latency.csv", "report.md"):
        assert (output / name).is_file()


def test_data_directories_are_gitignored():
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "data/datasets/" in ignore and "data/evaluation/" in ignore
