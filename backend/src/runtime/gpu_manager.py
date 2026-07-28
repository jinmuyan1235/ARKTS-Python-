"""GPU diagnostics and lightweight runtime controls."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.runtime.cuda_env import ensure_cuda_library_path


def sha256_file(path: str | Path) -> str | None:
    """Return SHA-256 for a model or fixture file when it exists."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        return None
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nvidia_smi_status() -> dict[str, Any]:
    """Return nvidia-smi GPU info without treating it as proof of framework GPU use."""
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,memory.total,memory.used",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {"available": False, "error": str(exc), "gpus": []}
    gpus: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5:
            gpus.append({
                "index": int(float(parts[0])),
                "name": parts[1],
                "driver_version": parts[2],
                "memory_total_mb": int(float(parts[3])),
                "memory_used_mb": int(float(parts[4])),
            })
    return {"available": bool(gpus), "gpus": gpus}


def gpu_selection_options() -> list[dict[str, Any]]:
    """Return user-facing GPU choices for Streamlit without importing ML frameworks."""
    status = nvidia_smi_status()
    options: list[dict[str, Any]] = [
        {
            "value": "auto",
            "label": "自动选择（优先可用 GPU）",
            "molscribe_device": "auto",
            "decimer_device": "auto",
            "visible_gpu_index": None,
        },
        {
            "value": "cpu",
            "label": "CPU（不使用 GPU）",
            "molscribe_device": "cpu",
            "decimer_device": "cpu",
            "visible_gpu_index": None,
        },
    ]
    for gpu in status.get("gpus", []):
        index = int(gpu.get("index", len(options) - 2))
        label = (
            f"GPU {index}: {gpu.get('name')} "
            f"({gpu.get('memory_used_mb')}/{gpu.get('memory_total_mb')} MB)"
        )
        options.append({
            "value": f"cuda:{index}",
            "label": label,
            "molscribe_device": f"cuda:{index}",
            "decimer_device": "gpu",
            "visible_gpu_index": str(index),
        })
    return options


def default_gpu_selection(options: list[dict[str, Any]] | None = None) -> str:
    """Return the UI default from environment variables and detected GPU options."""
    choices = options or gpu_selection_options()
    values = {str(option["value"]) for option in choices}
    requested = (os.getenv("OCSR_DEVICE") or "").strip().lower()
    decimer_requested = (os.getenv("DECIMER_DEVICE") or "").strip().lower()
    if requested in values:
        return requested
    if requested == "cuda" and "cuda:0" in values:
        return "cuda:0"
    if decimer_requested == "gpu" and "cuda:0" in values:
        return "cuda:0"
    return "auto" if "auto" in values else next(iter(values), "auto")


def torch_status(run_matrix_test: bool = False) -> dict[str, Any]:
    """Return PyTorch CUDA status and optionally execute a real CUDA matmul."""
    status: dict[str, Any] = {"installed": False, "cuda_available": False, "matrix_test": None}
    try:
        import torch
    except Exception as exc:
        status["error"] = str(exc)
        return status
    status.update({
        "installed": True,
        "version": getattr(torch, "__version__", None),
        "cuda_version": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "devices": [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())]
        if torch.cuda.is_available()
        else [],
    })
    if run_matrix_test and status["cuda_available"]:
        try:
            with torch.no_grad():
                tensor = torch.randn((1024, 1024), device="cuda:0")
                product = tensor @ tensor
                torch.cuda.synchronize()
                status["matrix_test"] = {
                    "ok": True,
                    "device": "cuda:0",
                    "sample": float(product[0, 0].item()),
                    "memory_allocated_mb": round(torch.cuda.memory_allocated(0) / 1024 / 1024, 2),
                    "memory_reserved_mb": round(torch.cuda.memory_reserved(0) / 1024 / 1024, 2),
                }
        except Exception as exc:
            status["matrix_test"] = {"ok": False, "error": str(exc)}
    return status


def tensorflow_status(run_matrix_test: bool = False) -> dict[str, Any]:
    """Return TensorFlow GPU status and optionally execute a real GPU matmul."""
    status: dict[str, Any] = {"installed": False, "gpu_available": False, "matrix_test": None}
    ensure_cuda_library_path(reexec=False)
    try:
        import tensorflow as tf
    except Exception as exc:
        status["error"] = str(exc)
        return status
    gpus = tf.config.list_physical_devices("GPU")
    status.update({
        "installed": True,
        "version": getattr(tf, "__version__", None),
        "gpu_available": bool(gpus),
        "gpus": [str(gpu) for gpu in gpus],
    })
    for gpu in gpus:
        try:
            tf.config.experimental.set_memory_growth(gpu, True)
        except Exception:
            pass
    if run_matrix_test and gpus:
        try:
            with tf.device("/GPU:0"):
                a = tf.random.normal((1024, 1024))
                product = tf.matmul(a, a)
                status["matrix_test"] = {
                    "ok": True,
                    "device": "/GPU:0",
                    "sample": float(product[0, 0].numpy()),
                }
        except Exception as exc:
            status["matrix_test"] = {"ok": False, "error": str(exc)}
    return status


def environment_status(run_matrix_test: bool = False) -> dict[str, Any]:
    """Return a structured environment report for UI and scripts."""
    return {
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "nvidia_smi": nvidia_smi_status(),
        "torch": torch_status(run_matrix_test=run_matrix_test),
        "tensorflow": tensorflow_status(run_matrix_test=run_matrix_test),
    }


def print_environment_json(run_matrix_test: bool = False) -> None:
    """Print diagnostics as UTF-8 JSON."""
    print(json.dumps(environment_status(run_matrix_test=run_matrix_test), ensure_ascii=False, indent=2))
