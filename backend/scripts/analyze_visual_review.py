"""Analyze the first completed round of OCSR visual review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.datasets.visual_review_analysis import analyze_visual_review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", default="data/review")
    parser.add_argument("--output-dir", default=None, help="Defaults to REVIEW_DIR/analysis.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = analyze_visual_review(args.review_dir, args.output_dir)
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "success", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
