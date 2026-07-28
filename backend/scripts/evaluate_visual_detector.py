"""Evaluate shared OpenCV candidate screening against frozen human visual labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.visual_detector import evaluate_visual_detector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--config", choices=["baseline", "candidate"], default="baseline")
    parser.add_argument(
        "--dataset-role",
        choices=["development", "holdout"],
        default=None,
        help=(
            "Dataset role. An explicit value overrides dataset_summary.json; "
            "otherwise the summary is used when present, falling back to development."
        ),
    )
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = evaluate_visual_detector(
            args.manifest,
            args.output,
            config_name=args.config,
            dataset_role=args.dataset_role,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "success", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
