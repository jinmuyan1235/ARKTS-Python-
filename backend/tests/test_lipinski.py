"""Tests for rule-based drug-likeness analysis."""

from src.chem.descriptors import calculate_descriptors
from src.chem.lipinski import evaluate_lipinski


def test_ethanol_passes_rules() -> None:
    result = evaluate_lipinski(calculate_descriptors("CCO"))
    assert result["passed"] is True
    assert result["violations"] == []


def test_large_hydrophobic_molecule_has_violations() -> None:
    descriptors = {
        "molecular_weight": 650,
        "logp": 7,
        "hbd": 0,
        "hba": 1,
        "rotatable_bonds": 14,
    }
    result = evaluate_lipinski(descriptors)
    assert result["passed"] is False
    assert {"MW > 500", "LogP > 5", "Rotatable Bonds > 10"} <= set(result["violations"])
