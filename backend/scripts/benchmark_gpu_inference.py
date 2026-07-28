"""Benchmark real OCSR backend latency and GPU memory use on local sample images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cuda_env import ensure_cuda_library_path

ensure_cuda_library_path(reexec=True)

from src.ocsr.recognizer import MoleculeRecognizer
from src.runtime.gpu_manager import nvidia_smi_status


def _sample_images(limit: int | None = None) -> list[Path]:
    samples = sorted((PROJECT_ROOT / "data" / "samples").glob("*.png"))
    return samples[:limit] if limit else samples


def _latency_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean_ms": None, "p50_ms": None, "p95_ms": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "mean_ms": round(statistics.mean(values), 3),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(ordered[p95_index], 3),
    }


def run_benchmark(backend: str, limit: int | None = None) -> dict[str, Any]:
    images = _sample_images(limit)
    recognizer = MoleculeRecognizer(backend)
    status = recognizer.status()
    if backend != "demo" and not status.get("available"):
        raise RuntimeError(f"{backend} 不可用：{status.get('message')}")
    rows = []
    before = nvidia_smi_status()
    started = time.perf_counter()
    for image_path in images:
        result = recognizer.recognize(image_path)
        rows.append({
            "filename": image_path.name,
            "status": result.status,
            "smiles": result.smiles,
            "message": result.message,
            "inference_time_ms": result.inference_time_ms,
            "device": result.device,
        })
    total_ms = round((time.perf_counter() - started) * 1000, 3)
    after = nvidia_smi_status()
    latencies = [float(row["inference_time_ms"]) for row in rows if row.get("inference_time_ms") is not None]
    return {
        "backend": backend,
        "sample_count": len(images),
        "total_time_ms": total_ms,
        "latency": _latency_stats(latencies),
        "gpu_before": before,
        "gpu_after": after,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark real OCSR inference.")
    parser.add_argument("--backend", default="ensemble", choices=["molscribe", "decimer", "ensemble", "demo"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    try:
        report = run_benchmark(args.backend, args.limit)
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
