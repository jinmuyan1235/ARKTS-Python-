"""Analyze trusted OCSR failures without reading frozen test by default."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.evaluation.trusted_diagnostics import analyze_trusted_failures
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1/manifest.csv")
    parser.add_argument("--evaluation-root", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1/diagnostics")
    parser.add_argument("--include-frozen-test", action="store_true")
    args = parser.parse_args()
    result = analyze_trusted_failures(args.manifest, args.evaluation_root, args.output, args.include_frozen_test)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
