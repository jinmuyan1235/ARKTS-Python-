"""Run or aggregate train/dev-only OCSR preprocessing experiments."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.runtime.cuda_env import ensure_cuda_library_path
ensure_cuda_library_path(reexec=True)
from src.evaluation.preprocessing_experiment import run_preprocessing_experiment
from src.ocsr.input_normalization import PROFILE_CONFIGS
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/datasets/ocsr-trusted-v0.1/manifest.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/ocsr-trusted-v0.1/dev_preprocessing")
    parser.add_argument("--backends", default="molscribe,decimer")
    parser.add_argument("--profiles", default=",".join(PROFILE_CONFIGS))
    parser.add_argument("--splits", default="dev")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-retry", action="store_true")
    args = parser.parse_args()
    backends = tuple(x.strip() for x in args.backends.split(",") if x.strip())
    profiles = tuple(x.strip() for x in args.profiles.split(",") if x.strip())
    if args.execute and (len(backends) > 1 or len(profiles) > 1):
        for backend in backends:
            for profile in profiles:
                run_dir = args.output / "runs" / backend / profile
                if (run_dir / "metrics.json").is_file():
                    continue
                command = [
                    sys.executable, str(Path(__file__).resolve()),
                    "--manifest", str(args.manifest), "--output", str(args.output),
                    "--backends", backend, "--profiles", profile,
                    "--splits", args.splits, "--execute",
                ]
                if args.no_retry: command.append("--no-retry")
                subprocess.run(command, check=True)
        args.execute = False
    result = run_preprocessing_experiment(
        args.manifest, args.output,
        backends,
        profiles,
        tuple(x.strip() for x in args.splits.split(",") if x.strip()),
        args.execute, not args.no_retry,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
