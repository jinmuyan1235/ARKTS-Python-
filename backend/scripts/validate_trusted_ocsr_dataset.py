"""Validate a frozen trusted OCSR dataset and fail on any integrity error."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.datasets.trusted_ocsr import validate_trusted_dataset

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1")
    args = parser.parse_args()
    result = validate_trusted_dataset(args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 2
if __name__ == "__main__": raise SystemExit(main())
