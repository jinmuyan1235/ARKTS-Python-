"""Generate clean RDKit sample images for the demo filename adapter."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import SAMPLE_DIR
from src.chem.mol_drawer import draw_molecule
from src.ocsr.demo_adapter import DemoOCSRAdapter


def main() -> None:
    """Render all built-in demonstration molecules into data/samples."""
    for name, smiles in DemoOCSRAdapter.SAMPLE_SMILES.items():
        path = draw_molecule(smiles, SAMPLE_DIR / f"{name}.png")
        print(path)


if __name__ == "__main__":
    main()
