"""CLI worker for isolated, terminable OCSR model inference."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ocsr.base import OCSRResult
from src.ocsr.decimer_adapter import DECIMERAdapter
from src.ocsr.ensemble import EnsembleOCSRAdapter
from src.ocsr.molscribe_adapter import MolScribeAdapter
from src.runtime.job_manager import MODEL_WORKER_RESULT_MARKER


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=["molscribe", "decimer", "ensemble"])
    parser.add_argument("--input", required=True, help="Image path to recognize.")
    parser.add_argument("--device", default=None, help="Backend-specific device override.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for TensorFlow.")
    return parser


def _worker_result(message: str) -> OCSRResult:
    return OCSRResult(None, None, "model_worker", "failed", message)


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.backend == "molscribe":
            adapter = MolScribeAdapter(
                device=args.device or os.environ.get("MOLSCRIBE_DEVICE") or os.environ.get("OCSR_DEVICE") or "auto",
                isolated_subprocess=False,
            )
        elif args.backend == "decimer":
            adapter = DECIMERAdapter(
                device=args.device or os.environ.get("DECIMER_DEVICE") or os.environ.get("OCSR_DEVICE") or "auto",
                isolated_subprocess=False,
                visible_gpu_index=args.visible_gpu_index or os.environ.get("CUDA_VISIBLE_DEVICES"),
            )
        else:
            runtime_config = {
                "molscribe_device": os.environ.get("MOLSCRIBE_DEVICE") or os.environ.get("OCSR_DEVICE") or "auto",
                "decimer_device": os.environ.get("DECIMER_DEVICE") or os.environ.get("OCSR_DEVICE") or "auto",
                "visible_gpu_index": args.visible_gpu_index or os.environ.get("CUDA_VISIBLE_DEVICES"),
            }
            adapter = EnsembleOCSRAdapter(runtime_config=runtime_config)
        result = adapter.recognize(args.input)
    except Exception as exc:
        result = _worker_result(str(exc))
    print(MODEL_WORKER_RESULT_MARKER + json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
