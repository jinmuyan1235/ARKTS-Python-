"""Run audit-only deterministic and model checks for an OCSR pending manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.datasets.machine_review import MachineReviewConfig, MachineReviewProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="data/ocsr_collections", help="Directory containing pending_manifest.csv.")
    parser.add_argument("--output-dir", default="data/review", help="Directory for machine review artifacts.")
    parser.add_argument("--reuse-predictions", action="store_true", help="Use saved candidate predictions instead of invoking OCSR backends.")
    parser.add_argument("--phash-distance", type=int, default=3)
    parser.add_argument("--redraw-similarity-threshold", type=float, default=0.58)
    parser.add_argument("--image-quality-threshold", type=float, default=0.55)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    processor = MachineReviewProcessor(
        args.dataset_root,
        output_dir=args.output_dir,
        rerun_models=not args.reuse_predictions,
        config_=MachineReviewConfig(
            perceptual_hash_distance=max(0, args.phash_distance),
            redraw_similarity_threshold=max(0.0, min(1.0, args.redraw_similarity_threshold)),
            image_quality_threshold=max(0.0, min(1.0, args.image_quality_threshold)),
        ),
    )
    print(json.dumps(processor.run(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
