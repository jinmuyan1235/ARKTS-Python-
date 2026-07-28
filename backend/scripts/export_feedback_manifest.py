"""Export verified correction feedback into an OCSR training/evaluation manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.feedback.store import export_feedback_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback-root", default=str(config.DATA_DIR / "feedback"))
    parser.add_argument("--output", default=str(config.DATA_DIR / "feedback" / "verified_manifest.csv"))
    parser.add_argument("--split", default="train", choices=["train", "validation", "test"])
    parser.add_argument("--review-status", default="verified", choices=["pending", "verified", "rejected"])
    parser.add_argument("--keep-duplicates", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = export_feedback_manifest(
        feedback_root=Path(args.feedback_root).expanduser().resolve(),
        output_manifest=Path(args.output).expanduser().resolve(),
        split=args.split,
        review_status=args.review_status,
        keep_duplicates=args.keep_duplicates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
