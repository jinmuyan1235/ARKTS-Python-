"""Verify local GPU, packages, models and backend readiness."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cuda_env import ensure_cuda_library_path

ensure_cuda_library_path(reexec=True)

import config
from src.ocsr.decimer_adapter import DECIMERAdapter
from src.ocsr.ensemble import EnsembleOCSRAdapter
from src.ocsr.molscribe_adapter import MolScribeAdapter
from src.runtime.gpu_manager import environment_status, sha256_file


def _package_version(name: str) -> str | None:
    for candidate in (name, name.lower(), name.upper()):
        try:
            return importlib.metadata.version(candidate)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _backend_status(load_models: bool = False) -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    for name, adapter in {
        "molscribe": MolScribeAdapter(),
        "decimer": DECIMERAdapter(),
        "ensemble": EnsembleOCSRAdapter(),
    }.items():
        try:
            if hasattr(adapter, "diagnose"):
                statuses[name] = adapter.diagnose(load_model=load_models)
            else:
                statuses[name] = adapter.status()
        except Exception as exc:
            statuses[name] = {"backend": name, "available": False, "message": str(exc)}
    return statuses


def build_report(load_models: bool = False) -> dict[str, Any]:
    model_path = config.MOLSCRIBE_MODEL_PATH
    decimer_cache = Path.home() / ".data" / "DECIMER-V2"
    decimer_model = decimer_cache / "DECIMER_model" / "saved_model.pb"
    decimer_hand_model = decimer_cache / "DECIMER_HandDrawn_model" / "saved_model.pb"
    return {
        "environment": environment_status(run_matrix_test=True),
        "packages": {
            "torch": _package_version("torch"),
            "tensorflow": _package_version("tensorflow"),
            "MolScribe": _package_version("MolScribe"),
            "DECIMER": _package_version("DECIMER"),
            "rdkit": _package_version("rdkit"),
            "opencv-python-headless": _package_version("opencv-python-headless"),
            "streamlit": _package_version("streamlit"),
        },
        "models": {
            "molscribe_model_path": str(model_path),
            "molscribe_model_exists": model_path.is_file(),
            "molscribe_model_sha256": sha256_file(model_path),
            "decimer_cache_dir": str(decimer_cache),
            "decimer_model_exists": decimer_model.is_file(),
            "decimer_model_saved_model_size": decimer_model.stat().st_size if decimer_model.is_file() else None,
            "decimer_handdrawn_model_exists": decimer_hand_model.is_file(),
            "decimer_handdrawn_saved_model_size": (
                decimer_hand_model.stat().st_size if decimer_hand_model.is_file() else None
            ),
        },
        "backends": _backend_status(load_models=load_models),
    }


def _strict_ok(report: dict[str, Any]) -> bool:
    env = report["environment"]
    torch_ok = bool((env.get("torch") or {}).get("cuda_available")) and bool((env.get("torch") or {}).get("matrix_test", {}).get("ok"))
    tf_ok = bool((env.get("tensorflow") or {}).get("gpu_available")) and bool(
        (env.get("tensorflow") or {}).get("matrix_test", {}).get("ok")
    )
    model_ok = bool((report.get("models") or {}).get("molscribe_model_exists"))
    return torch_ok and tf_ok and model_ok


def main() -> int:
    load_models = "--load-models" in sys.argv
    no_strict = "--no-strict" in sys.argv
    report = build_report(load_models=load_models)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not no_strict and not _strict_ok(report):
        print("GPU 或模型验证未全部通过；不会声明真实 GPU OCSR 已完成。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
