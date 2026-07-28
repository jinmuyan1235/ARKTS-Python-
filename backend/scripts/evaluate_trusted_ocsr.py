"""Evaluate MolScribe, DECIMER, or the frozen ensemble on trusted test data."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.runtime.cuda_env import ensure_cuda_library_path
ensure_cuda_library_path(reexec=True)
from src.evaluation.trusted_ocsr import backend_predictor_from_file, ensemble_predictor_from_files, evaluate_trusted_manifest

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1/manifest.csv")
    parser.add_argument("--backend", required=True, choices=["molscribe", "decimer", "ensemble"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--molscribe-predictions", type=Path)
    parser.add_argument("--decimer-predictions", type=Path)
    parser.add_argument("--reuse-predictions", type=Path, help="Recompute metrics from this backend's saved raw predictions.")
    parser.add_argument("--peak-gpu-memory-mib", type=float, help="Preserve a previously measured system-level GPU peak when replaying predictions.")
    parser.add_argument("--splits", default="test", help="Comma-separated manifest splits.")
    parser.add_argument("--purpose", default="formal_baseline", choices=["formal_baseline", "diagnostic", "profile_selection", "router_selection", "external_holdout"])
    parser.add_argument("--include-frozen-test", action="store_true", help="Explicitly allow test reads outside the default formal baseline run.")
    parser.add_argument("--preprocessing-profile", default="raw", choices=["raw", "alpha_flatten", "autocrop_and_pad", "scale_normalized", "contrast_normalized", "line_enhanced", "combined_normalized"])
    parser.add_argument("--retry-failures", action="store_true", help="Retry one failed sample in a rebuilt isolated model subprocess.")
    args = parser.parse_args()
    output = args.output or ROOT / f"data/evaluation/ocsr-trusted-v0.1/{args.backend}"
    predictor = None
    measure_gpu = True
    if args.reuse_predictions:
        predictor = backend_predictor_from_file(args.manifest.resolve().parent, args.reuse_predictions, args.backend)
        measure_gpu = False
    if args.backend == "ensemble":
        molscribe = args.molscribe_predictions or output.parent / "molscribe/predictions.csv"
        decimer = args.decimer_predictions or output.parent / "decimer/predictions.csv"
        if molscribe.is_file() and decimer.is_file():
            predictor = ensemble_predictor_from_files(args.manifest.resolve().parent, molscribe, decimer)
            measure_gpu = False
    result = evaluate_trusted_manifest(
        args.manifest, args.backend, output, predictor=predictor, limit=args.limit,
        measure_gpu=measure_gpu, peak_gpu_memory_mib=args.peak_gpu_memory_mib,
        splits=tuple(item.strip() for item in args.splits.split(",") if item.strip()),
        purpose=args.purpose,
        allow_frozen_test=(args.purpose == "formal_baseline" or args.include_frozen_test),
        preprocessing_profile=args.preprocessing_profile,
        retry_failures=args.retry_failures,
    )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
