"""Convert 50 trusted official images to the official MolScribe training format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ocsr.molscribe_finetune_feasibility import prepare_molscribe_probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--official-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_molscribe_probe(
        args.dataset.resolve(), args.output.resolve(), args.checkpoint.resolve(), args.official_source.resolve()
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
