"""Calculation of interpretable molecular descriptors."""

from __future__ import annotations

from typing import Any

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

from .smiles_validator import smiles_to_mol


def calculate_descriptors(smiles: str) -> dict[str, Any]:
    """Calculate basic RDKit descriptors for a valid SMILES string.

    Raises:
        ValueError: If RDKit cannot parse the supplied SMILES.
    """
    molecule = smiles_to_mol(smiles)
    if molecule is None:
        raise ValueError("无法计算性质：输入的 SMILES 无效。")
    return {
        "formula": rdMolDescriptors.CalcMolFormula(molecule),
        "molecular_weight": round(float(Descriptors.MolWt(molecule)), 2),
        "logp": round(float(Crippen.MolLogP(molecule)), 2),
        "tpsa": round(float(rdMolDescriptors.CalcTPSA(molecule)), 2),
        "hbd": int(Lipinski.NumHDonors(molecule)),
        "hba": int(Lipinski.NumHAcceptors(molecule)),
        "rotatable_bonds": int(Lipinski.NumRotatableBonds(molecule)),
        "ring_count": int(rdMolDescriptors.CalcNumRings(molecule)),
        "formal_charge": int(Chem.GetFormalCharge(molecule)),
        "fragment_count": int(len(Chem.GetMolFrags(molecule))),
        "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
    }
