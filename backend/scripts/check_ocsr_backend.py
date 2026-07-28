"""Print diagnostics for optional OCSR backends without crashing on missing deps."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import time
from typing import Any

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cuda_env import ensure_cuda_library_path

ensure_cuda_library_path(reexec=True)

import config
from src.chem.smiles_validator import validate_smiles
from src.ocsr.demo_adapter import DemoOCSRAdapter
from src.ocsr.decimer_adapter import DECIMERAdapter
from src.ocsr.ensemble import EnsembleOCSRAdapter
from src.ocsr.molscribe_adapter import MolScribeAdapter
from src.ocsr.recognizer import MoleculeRecognizer
from src.runtime.gpu_manager import environment_status
from src.runtime.health import run_production_health_check
from src.runtime.metadata import dependency_versions, git_commit, sha256_file


def _cuda_available() -> tuple[bool, str | None]:
    try:
        import torch

        return bool(torch.cuda.is_available()), None
    except Exception as exc:
        return False, str(exc)


def _rdkit_self_check() -> dict[str, Any]:
    try:
        validation = validate_smiles("CCO")
        return {
            "available": bool(validation["valid"]),
            "canonical_smiles": validation["canonical_smiles"],
            "message": "RDKit self-check passed." if validation["valid"] else validation["error"],
        }
    except Exception as exc:
        return {"available": False, "canonical_smiles": None, "message": str(exc)}


def _add_traceability(status: dict[str, Any]) -> dict[str, Any]:
    status["git_commit"] = git_commit()
    status["dependency_versions"] = dependency_versions()
    model_path = status.get("model_path")
    if model_path:
        status["model_sha256"] = sha256_file(model_path)
    return status


def check_backend(backend: str, load_model: bool = True) -> dict[str, Any]:
    if backend == "demo":
        cuda_available, torch_error = _cuda_available()
        adapter = DemoOCSRAdapter()
        status = adapter.status()
        status.update(
            {
                "python_version": platform.python_version(),
                "package_installed": True,
                "package_version": "built-in",
                "model_path": None,
                "model_exists": True,
                "model_loaded": True,
                "device": "cpu",
                "cuda_available": cuda_available,
            }
        )
        if torch_error:
            status["torch_error"] = torch_error
        return _add_traceability(status)
    if backend == "decimer":
        adapter = DECIMERAdapter()
        status = adapter.diagnose(load_model=load_model)
        status["python_version"] = platform.python_version()
        return _add_traceability(status)
    if backend == "ensemble":
        adapter = EnsembleOCSRAdapter()
        status = adapter.status()
        status["python_version"] = platform.python_version()
        return _add_traceability(status)
    adapter = MolScribeAdapter()
    status = adapter.diagnose(load_model=load_model)
    status["python_version"] = platform.python_version()
    return _add_traceability(status)


def warmup_backend(backend: str, image_path: str | Path) -> dict[str, Any]:
    started = time.perf_counter()
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "message": f"Warm-up image does not exist: {path}", "input": str(path)}
    try:
        result = MoleculeRecognizer(backend).recognize(path)
        validation = validate_smiles(result.smiles)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "ok": result.status == "success" and bool(validation["valid"]),
            "input": str(path),
            "backend": backend,
            "status": result.status,
            "message": result.message,
            "smiles": result.smiles,
            "rdkit_valid": validation["valid"],
            "canonical_smiles": validation["canonical_smiles"],
            "inference_time_ms": result.inference_time_ms,
            "total_warmup_time_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {"ok": False, "input": str(path), "backend": backend, "message": str(exc), "total_warmup_time_ms": elapsed_ms}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OCSR backend readiness.")
    parser.add_argument("--backend", choices=["molscribe", "demo", "decimer", "ensemble"], default=config.OCSR_BACKEND)
    parser.add_argument("--production", action="store_true", help="Require a real backend, RDKit self-check and warm-up success.")
    parser.add_argument("--warmup", action="store_true", help="Run one recognition pass on --warmup-input.")
    parser.add_argument("--warmup-input", default=str(PROJECT_ROOT / "data" / "samples" / "aspirin.png"))
    parser.add_argument("--no-load-model", action="store_true", help="Skip explicit model loading during diagnostics.")
    parser.add_argument("--no-cache", action="store_true", help="Do not reuse cached health-check results.")
    parser.add_argument("--force", action="store_true", help="Force a fresh health check even when a cache entry exists.")
    parser.add_argument("--molscribe-device", default=None, help="MolScribe device override, e.g. cpu or cuda:0.")
    parser.add_argument("--decimer-device", default=None, help="DECIMER device override: cpu, gpu or auto.")
    parser.add_argument("--visible-gpu-index", default=None, help="CUDA_VISIBLE_DEVICES index for DECIMER/TensorFlow.")
    args = parser.parse_args()
    production_check = bool(args.production or config.APP_MODE == "production")
    runtime_config = {
        "molscribe_device": args.molscribe_device,
        "decimer_device": args.decimer_device,
        "visible_gpu_index": args.visible_gpu_index,
    }
    status = run_production_health_check(
        args.backend,
        runtime_config={key: value for key, value in runtime_config.items() if value is not None},
        production=production_check,
        warmup=bool(args.warmup or production_check),
        load_model=not args.no_load_model,
        warmup_input=args.warmup_input,
        force=args.force,
        use_cache=not args.no_cache,
    )
    status["runtime"] = environment_status(run_matrix_test=False)
    exit_code = 0 if status.get("ready") else 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
