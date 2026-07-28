"""Analyze MolScribe/DECIMER overlap on train/dev without changing ensemble rules."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.evaluation.ensemble_dev_analysis import analyze_development_overlap
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--molscribe", type=Path, required=True)
    parser.add_argument("--decimer", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1/dev_ensemble_analysis")
    args = parser.parse_args()
    print(json.dumps(analyze_development_overlap(args.molscribe, args.decimer, args.output), ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
