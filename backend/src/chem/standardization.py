"""Auditable RDKit chemical standardization and identity helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from rdkit import Chem, rdBase
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize

import config
from src.chem.smiles_validator import suppress_rdkit_parse_errors, unsupported_structure_reason


STANDARDIZATION_PROFILES: dict[str, list[str]] = {
    "none": [],
    "conservative": ["cleanup", "normalize"],
    "parent": ["cleanup", "normalize", "metal_disconnector", "fragment_parent", "largest_fragment", "uncharge"],
    "tautomer_canonical": [
        "cleanup",
        "normalize",
        "metal_disconnector",
        "fragment_parent",
        "largest_fragment",
        "uncharge",
        "reionize",
        "tautomer_canonical",
    ],
}

PROFILE_DESCRIPTIONS = {
    "none": "Only parse and identify the molecule; no RDKit standardization transform is applied.",
    "conservative": "Apply RDKit Cleanup/Normalize without fragment deletion, uncharging or tautomer canonicalization.",
    "parent": "Apply parent-like cleanup, metal disconnection, salt/fragment parent selection and uncharging.",
    "tautomer_canonical": "Apply parent profile steps plus reionization and RDKit tautomer canonicalization.",
}

METAL_ATOMIC_NUMBERS = {
    3,
    4,
    11,
    12,
    13,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    55,
    56,
    57,
    72,
    73,
    74,
    75,
    76,
    77,
    78,
    79,
    80,
    81,
    82,
    83,
    87,
    88,
    89,
}


def utc_now_iso() -> str:
    """Return a UTC audit timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _canonical(mol: Chem.Mol | None, isomeric: bool = True) -> str | None:
    if mol is None:
        return None
    try:
        return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=isomeric)
    except Exception:
        return None


def _parse_mol(smiles: str | None) -> tuple[Chem.Mol | None, list[dict[str, Any]], str | None]:
    warnings: list[dict[str, Any]] = []
    if smiles is None or not isinstance(smiles, str) or not smiles.strip():
        return None, warnings, "SMILES 不能为空。"
    try:
        with suppress_rdkit_parse_errors():
            mol = Chem.MolFromSmiles(smiles.strip())
        if mol is not None:
            unsupported = unsupported_structure_reason(mol)
            if unsupported:
                warnings.append({"code": "unsupported_structure", "message": unsupported, "severity": "error"})
                return None, warnings, unsupported
            return mol, warnings, None
    except Exception as exc:
        warnings.append({"code": "parse_exception", "message": str(exc), "severity": "error"})
    try:
        with suppress_rdkit_parse_errors():
            unsanitized = Chem.MolFromSmiles(smiles.strip(), sanitize=False)
        if unsanitized is not None:
            for problem in Chem.DetectChemistryProblems(unsanitized):
                warnings.append({
                    "code": problem.GetType(),
                    "message": problem.Message(),
                    "severity": "error",
                })
    except Exception as exc:
        warnings.append({"code": "sanitize_probe_failed", "message": str(exc), "severity": "warning"})
    return None, warnings, "RDKit 无法解析该 SMILES，请检查原子、键、价态和括号。"


def _detect_chemistry_problems(mol: Chem.Mol) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    try:
        for problem in Chem.DetectChemistryProblems(mol):
            warnings.append({"code": problem.GetType(), "message": problem.Message(), "severity": "warning"})
    except Exception as exc:
        warnings.append({"code": "sanitize_warning_probe_failed", "message": str(exc), "severity": "warning"})
    return warnings


def _unspecified_double_bond_count(mol: Chem.Mol) -> int:
    count = 0
    for bond in mol.GetBonds():
        if bond.GetBondType() != Chem.BondType.DOUBLE or bond.IsInRing():
            continue
        if bond.GetStereo() != Chem.BondStereo.STEREONONE:
            continue
        begin = bond.GetBeginAtom()
        end = bond.GetEndAtom()
        begin_neighbors = [atom for atom in begin.GetNeighbors() if atom.GetIdx() != end.GetIdx()]
        end_neighbors = [atom for atom in end.GetNeighbors() if atom.GetIdx() != begin.GetIdx()]
        if begin_neighbors and end_neighbors:
            count += 1
    return count


def structure_warnings(mol: Chem.Mol | None) -> list[dict[str, Any]]:
    """Return structural quality warnings, not toxicology or pharmacology claims."""
    if mol is None:
        return []
    warnings = _detect_chemistry_problems(mol)
    fragments = Chem.GetMolFrags(mol)
    if len(fragments) > 1:
        warnings.append({
            "code": "multiple_fragments",
            "message": f"结构包含 {len(fragments)} 个片段；可能是盐、溶剂、配合物或混合输入。",
            "severity": "info",
        })
    charge = Chem.GetFormalCharge(mol)
    if charge != 0:
        warnings.append({"code": "nonzero_charge", "message": f"结构总形式电荷为 {charge}。", "severity": "info"})
    chiral_centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    unassigned_chiral = [center for center in chiral_centers if center[1] == "?"]
    if unassigned_chiral:
        warnings.append({
            "code": "unspecified_stereocenters",
            "message": f"存在 {len(unassigned_chiral)} 个未指定手性中心。",
            "severity": "info",
        })
    unspecified_double = _unspecified_double_bond_count(mol)
    if unspecified_double:
        warnings.append({
            "code": "unspecified_double_bond_stereo",
            "message": f"存在 {unspecified_double} 个可能未指定 E/Z 构型的双键。",
            "severity": "info",
        })
    isotope_atoms = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetIsotope()]
    if isotope_atoms:
        warnings.append({
            "code": "isotopes",
            "message": f"结构包含 {len(isotope_atoms)} 个同位素标记原子。",
            "severity": "info",
            "atom_indices": isotope_atoms,
        })
    metals = [atom.GetSymbol() for atom in mol.GetAtoms() if atom.GetAtomicNum() in METAL_ATOMIC_NUMBERS]
    if metals:
        warnings.append({
            "code": "metals",
            "message": f"结构包含金属或类金属原子：{', '.join(sorted(set(metals)))}。",
            "severity": "info",
        })
    return warnings


def _step_audit(profile: str, operation: str, input_smiles: str | None, output_smiles: str | None, warnings: list[str]) -> dict[str, Any]:
    return {
        "profile": profile,
        "operation": operation,
        "input_smiles": input_smiles,
        "output_smiles": output_smiles,
        "changed": input_smiles != output_smiles,
        "warnings": warnings,
        "timestamp": utc_now_iso(),
        "rdkit_version": rdBase.rdkitVersion,
    }


def _run_step(mol: Chem.Mol, profile: str, operation: str, function: Callable[[Chem.Mol], Chem.Mol]) -> tuple[Chem.Mol, dict[str, Any]]:
    input_smiles = _canonical(mol)
    warnings: list[str] = []
    try:
        output = function(Chem.Mol(mol))
        Chem.SanitizeMol(output)
    except Exception as exc:
        output = Chem.Mol(mol)
        warnings.append(str(exc))
    output_smiles = _canonical(output)
    return output, _step_audit(profile, operation, input_smiles, output_smiles, warnings)


def _operation_function(operation: str) -> Callable[[Chem.Mol], Chem.Mol]:
    if operation == "cleanup":
        return rdMolStandardize.Cleanup
    if operation == "normalize":
        normalizer = rdMolStandardize.Normalizer()
        return normalizer.normalize
    if operation == "metal_disconnector":
        disconnector = rdMolStandardize.MetalDisconnector()
        return disconnector.Disconnect
    if operation == "fragment_parent":
        return rdMolStandardize.FragmentParent
    if operation == "largest_fragment":
        chooser = rdMolStandardize.LargestFragmentChooser()
        return chooser.choose
    if operation == "uncharge":
        uncharger = rdMolStandardize.Uncharger()
        return uncharger.uncharge
    if operation == "reionize":
        reionizer = rdMolStandardize.Reionizer()
        return reionizer.reionize
    if operation == "tautomer_canonical":
        enumerator = rdMolStandardize.TautomerEnumerator()
        return enumerator.Canonicalize
    raise ValueError(f"Unsupported standardization operation: {operation}")


def _identity(raw_smiles: str | None, raw_mol: Chem.Mol, standardized_mol: Chem.Mol, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    raw_canonical = _canonical(raw_mol)
    standardized = _canonical(standardized_mol)
    isomeric = _canonical(standardized_mol, isomeric=True)
    identity: dict[str, Any] = {
        "raw_smiles": raw_smiles,
        "canonical_smiles": raw_canonical,
        "standardized_smiles": standardized,
        "isomeric_smiles": isomeric,
        "inchi": None,
        "inchikey": None,
        "formula": None,
        "formal_charge": None,
        "fragment_count": None,
        "stereocenter_count": None,
    }
    try:
        with suppress_rdkit_parse_errors():
            identity["inchi"] = Chem.MolToInchi(standardized_mol)
    except Exception as exc:
        warnings.append({"code": "inchi_unavailable", "message": str(exc), "severity": "warning"})
    try:
        with suppress_rdkit_parse_errors():
            identity["inchikey"] = Chem.MolToInchiKey(standardized_mol)
    except Exception as exc:
        warnings.append({"code": "inchikey_unavailable", "message": str(exc), "severity": "warning"})
    try:
        identity["formula"] = rdMolDescriptors.CalcMolFormula(standardized_mol)
    except Exception as exc:
        warnings.append({"code": "formula_failed", "message": str(exc), "severity": "warning"})
    identity["formal_charge"] = int(Chem.GetFormalCharge(standardized_mol))
    identity["fragment_count"] = int(len(Chem.GetMolFrags(standardized_mol)))
    identity["stereocenter_count"] = int(len(Chem.FindMolChiralCenters(standardized_mol, includeUnassigned=True, useLegacyImplementation=False)))
    return identity


def standardize_smiles(smiles: str | None, profile: str | None = None) -> dict[str, Any]:
    """Return an auditable standardization result for one SMILES string."""
    active_profile = (profile or config.CHEM_STANDARDIZATION_PROFILE or "conservative").strip().lower()
    if active_profile not in STANDARDIZATION_PROFILES:
        active_profile = "conservative"
    raw_mol, parse_warnings, error = _parse_mol(smiles)
    if raw_mol is None:
        return {
            "valid": False,
            "error": error,
            "chemical_identity": {
                "raw_smiles": smiles,
                "canonical_smiles": None,
                "standardized_smiles": None,
                "isomeric_smiles": None,
                "inchi": None,
                "inchikey": None,
                "formula": None,
                "formal_charge": None,
                "fragment_count": None,
                "stereocenter_count": None,
            },
            "standardization": {
                "profile": active_profile,
                "profile_description": PROFILE_DESCRIPTIONS[active_profile],
                "changed": False,
                "steps": [],
                "warnings": parse_warnings,
            },
            "structure_warnings": parse_warnings,
        }
    current = Chem.Mol(raw_mol)
    steps: list[dict[str, Any]] = []
    for operation in STANDARDIZATION_PROFILES[active_profile]:
        current, step = _run_step(current, active_profile, operation, _operation_function(operation))
        steps.append(step)
    warning_items = structure_warnings(current)
    identity = _identity(smiles, raw_mol, current, warning_items)
    raw_canonical = identity["canonical_smiles"]
    standardized = identity["standardized_smiles"]
    changed = bool(raw_canonical and standardized and raw_canonical != standardized)
    step_warnings = [
        {"code": "standardization_step_warning", "message": warning, "severity": "warning", "operation": step["operation"]}
        for step in steps
        for warning in step.get("warnings", [])
    ]
    warnings = [*warning_items, *step_warnings]
    return {
        "valid": standardized is not None,
        "error": None if standardized is not None else "标准化后无法生成 SMILES。",
        "chemical_identity": identity,
        "standardization": {
            "profile": active_profile,
            "profile_description": PROFILE_DESCRIPTIONS[active_profile],
            "changed": changed,
            "steps": steps,
            "warnings": warnings,
        },
        "structure_warnings": warnings,
    }


def identity_key(smiles: str | None, mode: str = "raw", profile: str | None = None) -> tuple[str | None, str | None]:
    """Return the comparison SMILES and InChIKey for raw or standardized identity matching."""
    result = standardize_smiles(smiles, profile)
    if not result["valid"]:
        return None, None
    identity = result["chemical_identity"]
    if mode == "standardized":
        return identity.get("standardized_smiles"), identity.get("inchikey")
    return identity.get("canonical_smiles"), identity.get("inchikey")
