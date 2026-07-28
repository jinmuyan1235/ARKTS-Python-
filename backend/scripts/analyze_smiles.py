"""Command-line analysis for one manually supplied SMILES string."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import OUTPUT_DIR
from src.analysis.molecule_report import MoleculeReportGenerator
from src.export.json_exporter import save_json, to_json_text


def main() -> int:
    """Analyze a SMILES string and write its JSON report."""
    parser = argparse.ArgumentParser(description="校验 SMILES 并计算基础分子性质")
    parser.add_argument("--smiles", required=True, help="待分析的 SMILES")
    parser.add_argument("--output", default=str(OUTPUT_DIR / "smiles_report.json"))
    args = parser.parse_args()
    report = MoleculeReportGenerator("manual", output_dir=Path(args.output).parent).generate(smiles=args.smiles)
    save_json(report, args.output)
    print(to_json_text(report))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
