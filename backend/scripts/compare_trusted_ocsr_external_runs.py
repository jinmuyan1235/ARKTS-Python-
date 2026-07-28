"""Compare named once-only ocsr-trusted-v0.2 evaluation directories."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.evaluation.trusted_external_comparison import compare_external_runs
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, help="NAME=EVALUATION_DIRECTORY; repeat for each frozen scheme")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.2/comparison")
    args = parser.parse_args()
    runs = {}
    for item in args.run:
        if "=" not in item: parser.error("--run must use NAME=PATH")
        name, path = item.split("=", 1); runs[name] = Path(path)
    print(json.dumps(compare_external_runs(runs, args.output), ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
