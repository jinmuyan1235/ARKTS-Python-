"""Run an OCSR benchmark from a CSV manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.dataset import ManifestValidationError
from src.evaluation.evaluator import EvaluationConfig, run_from_manifest
from src.evaluation.report_writer import create_run_directory, write_report_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate an OCSR backend against a CSV manifest.")
    parser.add_argument("--manifest", required=True, help="CSV manifest path.")
    parser.add_argument("--backend", default="demo", choices=["demo", "molscribe", "decimer", "ensemble"], help="OCSR backend.")
    parser.add_argument("--output", default="data/outputs/benchmark", help="Output root for benchmark runs.")
    parser.add_argument("--dataset-root", default=str(PROJECT_ROOT), help="Root directory image paths must stay inside.")
    parser.add_argument(
        "--preprocessing-strategy",
        default="backend-default",
        choices=["backend-default", "original", "gray", "denoised", "binary", "cropped", "deskewed", "normalized"],
        help="Image input strategy used before calling the backend.",
    )
    parser.add_argument("--similarity-threshold", type=float, default=0.95)
    parser.add_argument("--identity-comparison", choices=["raw", "standardized"], default="raw")
    parser.add_argument(
        "--standardization-profile",
        choices=["none", "conservative", "parent", "tautomer_canonical"],
        default="conservative",
    )
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N manifest rows.")
    parser.add_argument("--continue-on-error", action="store_true", default=True)
    parser.add_argument("--save-predictions", action="store_true", default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = EvaluationConfig(
        manifest=Path(args.manifest).expanduser().resolve(),
        dataset_root=Path(args.dataset_root).expanduser().resolve(),
        backend=args.backend,
        output=Path(args.output).expanduser().resolve(),
        preprocessing_strategy=args.preprocessing_strategy,
        similarity_threshold=args.similarity_threshold,
        identity_comparison=args.identity_comparison,
        standardization_profile=args.standardization_profile,
        limit=args.limit,
        continue_on_error=args.continue_on_error,
        save_predictions=args.save_predictions,
    )
    run_dir = create_run_directory(config.output, config.backend)
    config_payload = {
        "manifest": str(config.manifest),
        "dataset_root": str(config.dataset_root),
        "backend": config.backend,
        "output": str(config.output),
        "run_dir": str(run_dir),
        "preprocessing_strategy": config.preprocessing_strategy,
        "identity_comparison": config.identity_comparison,
        "standardization_profile": config.standardization_profile,
        "similarity_threshold": config.similarity_threshold,
        "limit": config.limit,
        "continue_on_error": config.continue_on_error,
        "save_predictions": config.save_predictions,
    }
    try:
        result = run_from_manifest(config)
    except ManifestValidationError as exc:
        (run_dir / "manifest_errors.txt").write_text(str(exc), encoding="utf-8")
        print(f"Manifest validation failed. Details written to {run_dir / 'manifest_errors.txt'}", file=sys.stderr)
        return 2
    outputs = write_report_bundle(run_dir, result, config_payload)
    print(json.dumps({"run_dir": str(run_dir), "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
