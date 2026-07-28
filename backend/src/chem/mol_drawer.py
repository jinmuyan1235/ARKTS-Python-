"""Molecular structure rendering helpers."""

from __future__ import annotations

from pathlib import Path

from rdkit.Chem import AllChem, Draw

from .smiles_validator import smiles_to_mol


def draw_molecule(smiles: str, output_path: str | Path, size: tuple[int, int] = (600, 450)) -> str:
    """Render a SMILES structure to a PNG file and return its absolute path."""
    molecule = smiles_to_mol(smiles)
    if molecule is None:
        raise ValueError("无法绘制分子：输入的 SMILES 无效。")
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    AllChem.Compute2DCoords(molecule)
    image = Draw.MolToImage(molecule, size=size, kekulize=True)
    image.save(destination, format="PNG")
    return str(destination)
