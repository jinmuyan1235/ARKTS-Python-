"""Tests for RDKit descriptor output."""

import pytest

from src.chem.descriptors import calculate_descriptors


def test_descriptor_fields_for_ethanol() -> None:
    result = calculate_descriptors("CCO")
    required = {
        "formula", "molecular_weight", "logp", "tpsa", "hbd", "hba",
        "rotatable_bonds", "heavy_atom_count",
    }
    assert required <= result.keys()
    assert result["formula"] == "C2H6O"
    assert result["molecular_weight"] == pytest.approx(46.07, abs=0.02)


def test_invalid_descriptor_input() -> None:
    with pytest.raises(ValueError, match="SMILES"):
        calculate_descriptors("not_a_smiles")
