"""Compare trusted MolScribe, DECIMER, and frozen-ensemble runs."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.evaluation.trusted_ocsr import compare_trusted_runs
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-root", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1/comparison")
    args = parser.parse_args(); print(json.dumps(compare_trusted_runs(args.evaluation_root, args.output), ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
