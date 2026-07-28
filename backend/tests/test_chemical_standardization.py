"""Tests for auditable chemical standardization and identity handling."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from src.analysis.batch_analyzer import BatchAnalyzer
from src.chem import standardization as standardization_module
from src.chem.standardization import standardize_smiles
from src.evaluation.metrics import enrich_prediction


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_neutral_molecule_identity_and_audit() -> None:
    result = standardize_smiles("OCC", "conservative")
    assert result["valid"] is True
    assert result["chemical_identity"]["raw_smiles"] == "OCC"
    assert result["chemical_identity"]["canonical_smiles"] == "CCO"
    assert result["chemical_identity"]["standardized_smiles"] == "CCO"
    assert result["chemical_identity"]["inchikey"]
    assert result["standardization"]["profile"] == "conservative"
    assert result["standardization"]["steps"]
    assert all("rdkit_version" in step for step in result["standardization"]["steps"])


def test_conservative_profile_keeps_salt_fragments_but_parent_removes_them() -> None:
    conservative = standardize_smiles("CC(=O)[O-].[Na+]", "conservative")
    parent = standardize_smiles("CC(=O)[O-].[Na+]", "parent")
    assert conservative["chemical_identity"]["fragment_count"] == 2
    assert conservative["chemical_identity"]["standardized_smiles"] == "CC(=O)[O-].[Na+]"
    assert any(warning["code"] == "multiple_fragments" for warning in conservative["structure_warnings"])
    assert parent["chemical_identity"]["standardized_smiles"] == "CC(=O)O"
    assert parent["standardization"]["changed"] is True


def test_charges_stereo_isotope_metal_and_double_bond_warnings() -> None:
    charged = standardize_smiles("[NH4+]", "none")
    assert any(warning["code"] == "nonzero_charge" for warning in charged["structure_warnings"])

    unspecified_chiral = standardize_smiles("CC(O)F", "none")
    assert any(warning["code"] == "unspecified_stereocenters" for warning in unspecified_chiral["structure_warnings"])

    specified_chiral = standardize_smiles("C[C@H](O)F", "none")
    assert specified_chiral["chemical_identity"]["stereocenter_count"] == 1
    assert not any(warning["code"] == "unspecified_stereocenters" for warning in specified_chiral["structure_warnings"])

    unspecified_double = standardize_smiles("FC=CF", "none")
    assert any(warning["code"] == "unspecified_double_bond_stereo" for warning in unspecified_double["structure_warnings"])

    specified_double = standardize_smiles("F/C=C/F", "none")
    assert not any(warning["code"] == "unspecified_double_bond_stereo" for warning in specified_double["structure_warnings"])

    isotope = standardize_smiles("[13CH4]", "none")
    assert any(warning["code"] == "isotopes" for warning in isotope["structure_warnings"])

    metal = standardize_smiles("[Na+].[Cl-]", "none")
    assert any(warning["code"] == "metals" for warning in metal["structure_warnings"])


def test_tautomer_profile_and_invalid_smiles() -> None:
    tautomer = standardize_smiles("C=C(O)C", "tautomer_canonical")
    assert tautomer["valid"] is True
    assert tautomer["chemical_identity"]["standardized_smiles"] == "CC(C)=O"
    assert any(step["operation"] == "tautomer_canonical" for step in tautomer["standardization"]["steps"])

    invalid = standardize_smiles("not-a-smiles", "conservative")
    assert invalid["valid"] is False
    assert invalid["chemical_identity"]["canonical_smiles"] is None
    assert invalid["standardization"]["steps"] == []


def test_inchi_unavailable_degrades_without_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        standardization_module.Chem,
        "MolToInchi",
        lambda _mol: (_ for _ in ()).throw(RuntimeError("inchi missing")),
    )
    monkeypatch.setattr(
        standardization_module.Chem,
        "MolToInchiKey",
        lambda _mol: (_ for _ in ()).throw(RuntimeError("inchikey missing")),
    )
    result = standardize_smiles("CCO", "conservative")
    assert result["valid"] is True
    assert result["chemical_identity"]["inchi"] is None
    assert result["chemical_identity"]["inchikey"] is None
    assert any(warning["code"] == "inchi_unavailable" for warning in result["structure_warnings"])
    assert any(warning["code"] == "inchikey_unavailable" for warning in result["structure_warnings"])


def test_benchmark_can_compare_raw_or_standardized_identity() -> None:
    row = {
        "ground_truth_smiles": "CC(=O)O",
        "predicted_smiles": "CC(=O)[O-].[Na+]",
        "recognition_success": True,
        "failure_reason": "",
    }
    raw = enrich_prediction(row, 0.95, identity_comparison="raw", standardization_profile="parent")
    standardized = enrich_prediction(row, 0.95, identity_comparison="standardized", standardization_profile="parent")
    assert raw["canonical_exact_match"] is False
    assert standardized["canonical_exact_match"] is True
    assert standardized["predicted_standardized_smiles"] == "CC(=O)O"


def test_batch_duplicate_summary_uses_identity_fields(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    source = PROJECT_ROOT / "data" / "samples" / "aspirin.png"
    (input_dir / "aspirin_one.png").write_bytes(source.read_bytes())
    (input_dir / "aspirin_two.png").write_bytes(source.read_bytes())
    Image.new("RGB", (24, 24), "white").save(input_dir / "unknown.png")

    result = BatchAnalyzer("demo", output_dir).analyze_folder(input_dir)
    assert result["summary"]["total"] == 3
    assert result["summary"]["duplicates"]["canonical_duplicate_count"] == 1
    assert result["summary"]["duplicates"]["standardized_duplicate_count"] == 1
    assert "inchikey_duplicate_count" in result["summary"]["duplicates"]
