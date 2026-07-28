"""Subprocess entry point for heavy production health checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cuda_env import ensure_cuda_library_path

ensure_cuda_library_path(reexec=True)

from src.runtime.health import HEALTH_WORKER_RESULT_MARKER, run_heavy_health_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=["demo", "molscribe", "decimer", "ensemble"])
    parser.add_argument("--runtime-json", default="{}")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--load-model", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--warmup-input", default="")
    return parser


def main() -> int:
    os.environ["OCSR_HEALTH_WORKER_PROCESS"] = "1"
    args = build_parser().parse_args()
    if args.backend != "ensemble":
        os.environ.setdefault("MOLSCRIBE_CHILD_PROCESS", "1")
        os.environ.setdefault("DECIMER_CHILD_PROCESS", "1")
    try:
        runtime_config = json.loads(args.runtime_json)
        if not isinstance(runtime_config, dict):
            runtime_config = {}
    except json.JSONDecodeError:
        runtime_config = {}
    payload = run_heavy_health_checks(
        args.backend,
        runtime_config,
        production=bool(args.production),
        load_model=bool(args.load_model),
        warmup=bool(args.warmup),
        warmup_path=args.warmup_input,
    )
    print(HEALTH_WORKER_RESULT_MARKER + json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
