"""Freeze reviewed visual OCSR artifacts into a named, checksummed version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.datasets.visual_dataset_snapshot import snapshot_visual_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--review-dir", default="data/review")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-role", choices=["development", "holdout"], default="development")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = snapshot_visual_dataset(
            version=args.version, review_dir=args.review_dir, output=args.output,
            dataset_role=args.dataset_role,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "success", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
