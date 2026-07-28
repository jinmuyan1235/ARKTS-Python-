"""Evaluate baseline or candidate raw bbox proposals on frozen page truth."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.page_proposals import evaluate_page_proposals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/datasets/visual-page-holdout-v0.1")
    parser.add_argument("--proposal-config", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    args = parser.parse_args()
    metrics = evaluate_page_proposals(
        Path(args.dataset), Path(args.output), proposal_config=args.proposal_config,
        iou_threshold=args.iou_threshold,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
