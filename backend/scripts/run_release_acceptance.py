"""Run fixed OCSR release acceptance gates against a reviewed manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.dataset import ManifestValidationError, load_manifest
from src.evaluation.evaluator import OCSREvaluator
from src.evaluation.release_gate import (
    collect_release_error_rows,
    evaluate_release_gates,
    write_csv,
    write_release_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-version", required=True, help="Release label, e.g. v0.1.")
    parser.add_argument("--manifest", default="data/ocsr_real_acceptance/manifest.csv")
    parser.add_argument("--dataset-root", default="data/ocsr_real_acceptance")
    parser.add_argument("--output-root", default="benchmark/releases")
    parser.add_argument("--backends", default="molscribe", help="Comma-separated backends, e.g. molscribe,ensemble.")
    parser.add_argument(
        "--preprocessing-strategy",
        default="backend-default",
        choices=["backend-default", "original", "gray", "denoised", "binary", "cropped", "deskewed", "normalized"],
    )
    parser.add_argument("--similarity-threshold", type=float, default=0.95)
    parser.add_argument("--identity-comparison", choices=["raw", "standardized"], default="raw")
    parser.add_argument(
        "--standardization-profile",
        choices=["none", "conservative", "parent", "tautomer_canonical"],
        default="conservative",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--allow-incomplete-metadata", action="store_true")
    parser.add_argument("--allow-failed-gates", action="store_true")
    parser.add_argument("--force", action="store_true", help="Allow writing into a non-empty release directory.")
    parser.add_argument("--positive-sample-count-min", type=int, default=None)
    parser.add_argument("--negative-sample-count-min", type=int, default=None)
    parser.add_argument("--independent-source-document-count-min", type=int, default=None)
    parser.add_argument("--unique-molecule-count-min", type=int, default=None)
    parser.add_argument("--unique-scaffold-count-min", type=int, default=None)
    parser.add_argument("--verified-sample-rate-min", type=float, default=None)
    parser.add_argument("--missing-image-count-max", type=int, default=None)
    parser.add_argument("--checksum-error-count-max", type=int, default=None)
    return parser.parse_args()


def gate_thresholds_from_args(args: argparse.Namespace) -> dict[str, float]:
    """Return only release gate threshold overrides explicitly supplied on the CLI."""
    mapping = {
        "positive_sample_count_min": args.positive_sample_count_min,
        "negative_sample_count_min": args.negative_sample_count_min,
        "independent_source_document_count_min": args.independent_source_document_count_min,
        "unique_molecule_count_min": args.unique_molecule_count_min,
        "unique_scaffold_count_min": args.unique_scaffold_count_min,
        "verified_sample_rate_min": args.verified_sample_rate_min,
        "missing_image_count_max": args.missing_image_count_max,
        "checksum_error_count_max": args.checksum_error_count_max,
    }
    return {key: value for key, value in mapping.items() if value is not None}


def run_release_acceptance(
    release_version: str,
    manifest: str | Path,
    dataset_root: str | Path,
    output_root: str | Path,
    backends: list[str],
    preprocessing_strategy: str = "backend-default",
    similarity_threshold: float = 0.95,
    identity_comparison: str = "raw",
    standardization_profile: str = "conservative",
    limit: int | None = None,
    require_real_metadata: bool = True,
    force: bool = False,
    gate_thresholds: dict[str, float] | None = None,
) -> dict:
    """Run all requested backends and write a fixed release report bundle."""
    release_dir = Path(output_root).expanduser().resolve() / release_version
    if release_dir.exists() and any(release_dir.iterdir()) and not force:
        raise RuntimeError(f"Release directory is not empty: {release_dir}. Use --force to update it.")
    release_dir.mkdir(parents=True, exist_ok=True)
    samples = load_manifest(manifest, dataset_root, require_real_metadata=require_real_metadata)
    if limit is not None:
        samples = samples[:limit]
    if not samples:
        raise RuntimeError("Release acceptance manifest contains no samples.")

    backend_payloads: dict[str, dict] = {}
    all_errors: list[dict] = []
    for backend in backends:
        evaluator = OCSREvaluator(
            backend=backend,
            preprocessing_strategy=preprocessing_strategy,
            similarity_threshold=similarity_threshold,
            identity_comparison=identity_comparison,
            standardization_profile=standardization_profile,
            continue_on_error=True,
        )
        result = evaluator.run(samples)
        gates = evaluate_release_gates(result["metrics"], thresholds=gate_thresholds)
        payload = {"metadata": result["metadata"], "metrics": result["metrics"], "gates": gates}
        backend_payloads[backend] = payload
        (release_dir / f"{backend}_metrics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_csv(release_dir / f"{backend}_predictions.csv", result["rows"])
        all_errors.extend(collect_release_error_rows(result["rows"], backend))

    write_csv(release_dir / "errors.csv", all_errors)
    config_payload = {
        "release_version": release_version,
        "manifest": str(Path(manifest).expanduser().resolve()),
        "dataset_root": str(Path(dataset_root).expanduser().resolve()),
        "backends": backends,
        "preprocessing_strategy": preprocessing_strategy,
        "similarity_threshold": similarity_threshold,
        "identity_comparison": identity_comparison,
        "standardization_profile": standardization_profile,
        "limit": limit,
        "require_real_metadata": require_real_metadata,
        "gate_thresholds": gate_thresholds or {},
    }
    (release_dir / "release_config.json").write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_release_report(release_dir / "report.md", release_version, backend_payloads, all_errors)
    return {
        "release_dir": str(release_dir),
        "passed": all(payload["gates"]["passed"] for payload in backend_payloads.values()),
        "backends": backend_payloads,
        "error_count": len(all_errors),
    }


def main() -> int:
    args = parse_args()
    backends = [item.strip().lower() for item in args.backends.split(",") if item.strip()]
    try:
        result = run_release_acceptance(
            release_version=args.release_version,
            manifest=args.manifest,
            dataset_root=args.dataset_root,
            output_root=args.output_root,
            backends=backends,
            preprocessing_strategy=args.preprocessing_strategy,
            similarity_threshold=args.similarity_threshold,
            identity_comparison=args.identity_comparison,
            standardization_profile=args.standardization_profile,
            limit=args.limit,
            require_real_metadata=not args.allow_incomplete_metadata,
            force=args.force,
            gate_thresholds=gate_thresholds_from_args(args),
        )
    except ManifestValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"release_dir": result["release_dir"], "passed": result["passed"], "error_count": result["error_count"]}, ensure_ascii=False, indent=2))
    if not result["passed"] and not args.allow_failed_gates:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
