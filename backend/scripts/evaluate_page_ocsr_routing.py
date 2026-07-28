"""Evaluate page proposal + crop-screening routing without running an OCSR model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.page_ocsr_routing import evaluate_page_ocsr_routing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--proposal-config", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--crop-screening-config", choices=["baseline", "candidate"], default="candidate")
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = evaluate_page_ocsr_routing(
        Path(args.dataset),
        Path(args.output),
        proposal_config=args.proposal_config,
        crop_screening_config=args.crop_screening_config,
        iou_threshold=args.iou_threshold,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
