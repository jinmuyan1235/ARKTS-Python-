"""Tests for SMILES validation and canonicalization."""

from src.chem.smiles_validator import canonicalize_smiles, validate_smiles


def test_valid_smiles() -> None:
    result = validate_smiles("CCO")
    assert result["valid"] is True
    assert result["canonical_smiles"] == "CCO"
    assert result["error"] is None


def test_invalid_smiles() -> None:
    result = validate_smiles("C1(CC")
    assert result["valid"] is False
    assert result["canonical_smiles"] is None
    assert result["error"]


def test_expected_parse_failure_does_not_spam_stderr(capfd) -> None:
    result = validate_smiles("C1(CC")
    captured = capfd.readouterr()
    assert result["valid"] is False
    assert "SMILES Parse Error" not in captured.err


def test_wildcard_smiles_is_rejected_before_analysis() -> None:
    result = validate_smiles("*")
    assert result["valid"] is False
    assert result["canonical_smiles"] is None
    assert "通配符" in result["error"] or "查询原子" in result["error"]


def test_canonicalize_aspirin() -> None:
    assert canonicalize_smiles("CC(=O)OC1=CC=CC=C1C(=O)O") == "CC(=O)Oc1ccccc1C(=O)O"
