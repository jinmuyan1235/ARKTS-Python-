"""Production startup health checks for OCSR workflows."""

from __future__ import annotations

import gc
from datetime import datetime, timezone
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import config
from src.chem.smiles_validator import validate_smiles
from src.export.structure_exporter import mol_text, sdf_text
from src.runtime.gpu_manager import environment_status
from src.runtime.job_manager import run_json_command
from src.runtime.metadata import dependency_versions, git_commit
from src.utils.file_utils import ensure_directory


CHECK_PASS = "pass"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"
CHECK_SKIP = "skip"
HEALTH_WORKER_RESULT_MARKER = "HEALTH_WORKER_RESULT_JSON="


def run_production_health_check(
    backend: str | None = None,
    runtime_config: Mapping[str, Any] | None = None,
    production: bool | None = None,
    warmup: bool | None = None,
    load_model: bool | None = None,
    warmup_input: str | Path | None = None,
    force: bool = False,
    use_cache: bool = True,
    cache_ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Run cached production readiness checks for image/document/batch OCSR."""
    selected_backend = (backend or config.OCSR_BACKEND).strip().lower()
    runtime = dict(runtime_config or {})
    is_production = config.IS_PRODUCTION_MODE if production is None else bool(production)
    should_warmup = (config.PRODUCTION_HEALTH_WARMUP if is_production else False) if warmup is None else bool(warmup)
    should_load_model = (
        config.PRODUCTION_HEALTH_LOAD_MODEL if is_production else False
    ) if load_model is None else bool(load_model)
    ttl = config.PRODUCTION_HEALTH_CACHE_TTL_SECONDS if cache_ttl_seconds is None else int(cache_ttl_seconds)
    warmup_path = Path(warmup_input or config.PRODUCTION_HEALTH_WARMUP_INPUT).expanduser().resolve()

    model_fingerprint = _model_fingerprint_for_cache(selected_backend)
    cache_key = _cache_key(
        selected_backend,
        runtime,
        model_fingerprint,
        should_load_model,
        should_warmup,
        warmup_path,
    )
    if use_cache and not force:
        cached = _read_cached_health(cache_key, ttl)
        if cached is not None:
            cached["cached"] = True
            cached.setdefault("model_load_count", 0)
            cached.setdefault("adapter_reused_for_warmup", False)
            cached.setdefault("peak_memory_available", False)
            return cached

    started = time.perf_counter()
    checks: list[dict[str, Any]] = []
    checks.append(_rdkit_check())
    checks.append(_settings_warnings_check())
    checks.extend(_writable_directory_checks())
    checks.append(_structure_export_check())

    heavy = _run_backend_health(
        selected_backend,
        runtime,
        production=is_production,
        load_model=should_load_model,
        warmup=should_warmup,
        warmup_path=warmup_path,
    )
    checks.extend(heavy.get("checks") or [])
    backend_status = heavy.get("backend_status") or {}
    worker = heavy.get("worker") or {}
    model_path = _primary_model_path(backend_status)
    model_sha = _model_sha_from_status(backend_status)

    failures = [check for check in checks if check["status"] == CHECK_FAIL]
    warnings = [check for check in checks if check["status"] == CHECK_WARN]
    ready = not failures
    image_workflows_enabled = bool((not is_production) or ready)
    payload = {
        "schema_version": 1,
        "created_at": _now(),
        "cached": False,
        "cache_key": cache_key,
        "app_mode": config.APP_MODE,
        "production": is_production,
        "backend": selected_backend,
        "runtime_config": runtime,
        "ready": ready,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "checks": checks,
        "failures": [str(check.get("message") or check["name"]) for check in failures],
        "warnings": [str(check.get("message") or check["name"]) for check in warnings],
        "capabilities": {
            "smiles_manual": True,
            "image_recognition": image_workflows_enabled,
            "document_recognition": image_workflows_enabled,
            "batch_recognition": image_workflows_enabled,
            "history": True,
            "review_queue": True,
        },
        "backend_status": backend_status,
        "model_load_count": int(heavy.get("model_load_count") or 0),
        "adapter_reused_for_warmup": bool(heavy.get("adapter_reused_for_warmup")),
        "peak_memory_available": bool(heavy.get("peak_memory_available")),
        "model_path": str(model_path) if model_path else None,
        "model_sha256": model_sha,
        "model_fingerprint": model_fingerprint,
        "worker": worker,
        "config_warnings": [check["message"] for check in checks if check.get("name") == "config.settings" and check.get("status") == CHECK_WARN],
        "dependency_versions": dependency_versions(),
        "git_commit": git_commit(),
        "repair_suggestions": _repair_suggestions(selected_backend, checks, is_production),
    }
    if use_cache:
        _write_cached_health(payload)
    return payload


def image_workflows_enabled(health: Mapping[str, Any] | None) -> bool:
    """Return whether image/document/batch recognition should be enabled."""
    if not health:
        return not config.IS_PRODUCTION_MODE
    capabilities = health.get("capabilities") if isinstance(health, Mapping) else {}
    if isinstance(capabilities, Mapping) and "image_recognition" in capabilities:
        return bool(capabilities.get("image_recognition"))
    return bool((not health.get("production")) or health.get("ready"))


def health_summary(health: Mapping[str, Any]) -> dict[str, Any]:
    """Return compact fields for sidebar or logs."""
    checks = list(health.get("checks") or [])
    return {
        "ready": bool(health.get("ready")),
        "backend": health.get("backend"),
        "created_at": health.get("created_at"),
        "cached": bool(health.get("cached")),
        "pass_count": sum(1 for item in checks if item.get("status") == CHECK_PASS),
        "warn_count": sum(1 for item in checks if item.get("status") == CHECK_WARN),
        "fail_count": sum(1 for item in checks if item.get("status") == CHECK_FAIL),
    }


def _backend_status(backend: str, runtime_config: Mapping[str, Any], load_model: bool) -> dict[str, Any]:
    try:
        if backend == "demo":
            from src.ocsr.demo_adapter import DemoOCSRAdapter

            status = DemoOCSRAdapter().status()
            status.update({"package_installed": True, "package_version": "built-in", "model_loaded": True, "device": "cpu"})
            return status
        if backend == "molscribe":
            from src.ocsr.molscribe_adapter import MolScribeAdapter

            adapter = MolScribeAdapter(device=runtime_config.get("molscribe_device"))
            return adapter.diagnose(load_model=load_model)
        if backend == "decimer":
            from src.ocsr.decimer_adapter import DECIMERAdapter

            adapter = DECIMERAdapter(
                device=runtime_config.get("decimer_device"),
                visible_gpu_index=runtime_config.get("visible_gpu_index"),
            )
            return adapter.diagnose(load_model=load_model)
        if backend == "ensemble":
            from src.ocsr.ensemble import EnsembleOCSRAdapter

            adapter = EnsembleOCSRAdapter(runtime_config=runtime_config)
            status = adapter.status()
            status["load_model_requested"] = bool(load_model)
            return status
        from src.ocsr.recognizer import MoleculeRecognizer

        recognizer = MoleculeRecognizer(backend, runtime_config=dict(runtime_config))
        return recognizer.status()
    except Exception as exc:
        return {"backend": backend, "available": False, "message": str(exc), "exception": exc.__class__.__name__}


def _build_health_runtime(backend: str, runtime_config: Mapping[str, Any]) -> Any:
    if backend == "demo":
        from src.ocsr.demo_adapter import DemoOCSRAdapter

        return DemoOCSRAdapter()
    from src.ocsr.recognizer import MoleculeRecognizer

    recognizer = MoleculeRecognizer(backend, runtime_config=dict(runtime_config))
    _configure_health_adapter(backend, _runtime_adapter(recognizer))
    return recognizer


def _runtime_adapter(runtime: Any) -> Any:
    return getattr(runtime, "adapter", runtime)


def _configure_health_adapter(backend: str, adapter: Any) -> None:
    if backend != "ensemble":
        return
    if hasattr(adapter, "parallel"):
        adapter.parallel = False
    setattr(adapter, "release_after_each_backend", True)


def _backend_status_from_runtime(backend: str, runtime: Any, load_model: bool) -> dict[str, Any]:
    try:
        adapter = _runtime_adapter(runtime)
        if backend == "demo":
            status = adapter.status()
            status.update({"package_installed": True, "package_version": "built-in", "model_loaded": True, "device": "cpu"})
            return status
        diagnose = getattr(adapter, "diagnose", None)
        if callable(diagnose):
            return dict(diagnose(load_model=load_model))
        if hasattr(runtime, "status"):
            status = dict(runtime.status())
        elif hasattr(adapter, "status"):
            status = dict(adapter.status())
        else:
            status = {"backend": backend, "available": False, "message": "Backend does not expose status()."}
        status["load_model_requested"] = bool(load_model)
        return status
    except Exception as exc:
        return {"backend": backend, "available": False, "message": str(exc), "exception": exc.__class__.__name__}


def _status_model_loaded(status: Mapping[str, Any]) -> bool:
    if status.get("model_loaded") or status.get("initialization_success"):
        return True
    for child in status.get("child_statuses") or []:
        if isinstance(child, Mapping) and (child.get("model_loaded") or child.get("initialization_success")):
            return True
    return False


def _reported_model_load_count(runtime: Any, status: Mapping[str, Any], fallback: int) -> int:
    values: list[Any] = [status.get("health_model_load_count"), status.get("model_load_count")]
    adapter = _runtime_adapter(runtime)
    values.extend([getattr(adapter, "health_model_load_count", None), getattr(runtime, "health_model_load_count", None)])
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(fallback)


def _release_cached_child_adapters(runtime: Any) -> None:
    release = getattr(_runtime_adapter(runtime), "release_adapters", None)
    if callable(release):
        release()


def _release_health_runtime(runtime: Any) -> None:
    if runtime is None:
        return
    _release_cached_child_adapters(runtime)
    adapter = _runtime_adapter(runtime)
    seen: set[int] = set()
    for target in (adapter, runtime):
        ident = id(target)
        if ident in seen:
            continue
        seen.add(ident)
        close = getattr(target, "close", None)
        if callable(close):
            close()
    if hasattr(runtime, "adapter"):
        runtime.adapter = None
    gc.collect()
    _cleanup_framework_caches_if_worker()


def _cleanup_framework_caches_if_worker() -> None:
    if os.environ.get("OCSR_HEALTH_WORKER_PROCESS") != "1":
        return
    torch = sys.modules.get("torch")
    if torch is not None:
        cuda = getattr(torch, "cuda", None)
        try:
            if cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available():
                cuda.empty_cache()
        except Exception:
            pass
    tensorflow = sys.modules.get("tensorflow")
    if tensorflow is not None:
        try:
            tensorflow.keras.backend.clear_session()
        except Exception:
            pass


def _backend_checks(backend: str, status: Mapping[str, Any], production: bool) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if production and backend == "demo":
        checks.append(_check("backend.policy", CHECK_FAIL, "生产模式禁止使用 demo 图像识别后端。"))
    available = bool(status.get("available"))
    checks.append(
        _check(
            "backend.available",
            CHECK_PASS if available else CHECK_FAIL,
            str(status.get("message") or ("后端可用。" if available else "后端不可用。")),
            {"backend": backend},
        )
    )
    child_statuses = list(status.get("child_statuses") or []) if backend == "ensemble" else []
    if backend == "ensemble":
        package_backends = [
            str(item.get("backend"))
            for item in child_statuses
            if item.get("available") and item.get("package_installed")
        ]
        package_ok = len(package_backends) >= 2
        package_message = (
            f"联合识别使用的子后端包可导入：{', '.join(package_backends)}。"
            if package_ok
            else "联合识别需要至少两个可导入的真实子后端包。"
        )
        package_details = {"child_backends": package_backends}
    else:
        package_ok = bool(status.get("package_installed", backend == "demo"))
        package_message = "后端包可导入。" if package_ok else "后端包未安装或无法导入。"
        package_details = {"package_version": status.get("package_version")}
    checks.append(
        _check(
            "backend.package",
            CHECK_PASS if package_ok else CHECK_FAIL,
            package_message,
            package_details,
        )
    )
    model_path = _primary_model_path(status)
    if model_path:
        exists = model_path.is_file()
        checks.append(
            _check(
                "backend.model_file",
                CHECK_PASS if exists else CHECK_FAIL,
                f"模型文件存在：{model_path}" if exists else f"模型文件不存在：{model_path}",
                {
                    "model_path": str(model_path),
                    "model_sha256": status.get("model_sha256"),
                    "model_fingerprint": _path_fingerprint(model_path) if exists else None,
                },
            )
        )
    elif backend in {"decimer", "demo", "ensemble"}:
        checks.append(_check("backend.model_file", CHECK_SKIP, "该后端没有显式本地模型文件路径。"))
    else:
        checks.append(_check("backend.model_file", CHECK_WARN, "未能读取模型文件路径。"))
    if status.get("model_loaded") or status.get("initialization_success"):
        checks.append(_check("backend.model_load", CHECK_PASS, "模型/预测器已成功加载。"))
    elif status.get("load_error"):
        checks.append(_check("backend.model_load", CHECK_FAIL, str(status.get("load_error"))))
    else:
        checks.append(_check("backend.model_load", CHECK_SKIP, "本次未强制加载模型。"))
    checks.append(_device_check(backend, status))
    if backend == "ensemble":
        available_children = [item.get("backend") for item in child_statuses if item.get("available")]
        checks.append(
            _check(
                "backend.ensemble_children",
                CHECK_PASS if len(available_children) >= 2 else CHECK_FAIL,
                f"可用子后端：{', '.join(map(str, available_children)) or '无'}",
                {"available_children": available_children, "child_statuses": child_statuses},
            )
        )
    return checks


def _device_check(backend: str, status: Mapping[str, Any]) -> dict[str, Any]:
    requested = status.get("requested_device") or status.get("device")
    if backend == "molscribe":
        torch_status = status.get("torch") if isinstance(status.get("torch"), Mapping) else {}
        if str(requested).startswith("cuda") or str(requested) == "gpu":
            ok = bool(torch_status.get("cuda_available") or status.get("cuda_available"))
            return _check(
                "runtime.device",
                CHECK_PASS if ok else CHECK_FAIL,
                "PyTorch CUDA 可用。" if ok else "请求了 CUDA 设备，但 PyTorch CUDA 不可用。",
                {"requested_device": requested, "torch": torch_status},
            )
    if backend == "decimer":
        tf_status = status.get("tensorflow") if isinstance(status.get("tensorflow"), Mapping) else {}
        if requested == "gpu":
            ok = bool(tf_status.get("gpu_available") or status.get("gpu_available"))
            return _check(
                "runtime.device",
                CHECK_PASS if ok else CHECK_FAIL,
                "TensorFlow GPU 可用。" if ok else "请求了 GPU 运行 DECIMER，但 TensorFlow GPU 不可用。",
                {"requested_device": requested, "tensorflow": tf_status},
            )
        if requested == "auto":
            gpu_available = bool(tf_status.get("gpu_available") or status.get("gpu_available"))
            requires_gpu = bool(status.get("strict_mode") or config.OCSR_GPU_REQUIRED)
            if gpu_available:
                return _check(
                    "runtime.device",
                    CHECK_PASS,
                    "自动选择已解析为 TensorFlow GPU。",
                    {"requested_device": requested, "resolved_device": "gpu", "tensorflow": tf_status},
                )
            return _check(
                "runtime.device",
                CHECK_FAIL if requires_gpu else CHECK_PASS,
                (
                    "自动选择未检测到 TensorFlow GPU，且当前配置要求 GPU。"
                    if requires_gpu
                    else "自动选择未检测到 TensorFlow GPU，已回退 CPU。"
                ),
                {"requested_device": requested, "resolved_device": "cpu", "tensorflow": tf_status},
            )
    return _check("runtime.device", CHECK_PASS, f"请求设备：{requested or 'auto'}", {"device": status.get("device")})


def _rdkit_check() -> dict[str, Any]:
    try:
        validation = validate_smiles("CCO")
        return _check(
            "rdkit",
            CHECK_PASS if validation.get("valid") else CHECK_FAIL,
            "RDKit 可解析基础 SMILES。" if validation.get("valid") else str(validation.get("error")),
            {"canonical_smiles": validation.get("canonical_smiles")},
        )
    except Exception as exc:
        return _check("rdkit", CHECK_FAIL, f"RDKit 自检失败：{exc}")


def _settings_warnings_check() -> dict[str, Any]:
    warnings = config.validate_settings(config.SETTINGS)
    if warnings:
        return _check("config.settings", CHECK_WARN, "；".join(warnings), {"warnings": warnings})
    return _check("config.settings", CHECK_PASS, "配置未发现非致命告警。")


def _writable_directory_checks() -> list[dict[str, Any]]:
    return [
        _writable_directory_check("output_dir", config.OUTPUT_DIR),
        _writable_directory_check("runs_dir", config.RUNS_DIR),
        _writable_directory_check("document_output_dir", config.DOCUMENT_OUTPUT_DIR),
    ]


def _writable_directory_check(name: str, directory: str | Path) -> dict[str, Any]:
    path = Path(directory).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".health_", suffix=".tmp", dir=path, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return _check(f"filesystem.{name}", CHECK_PASS, f"目录可写：{path}", {"path": str(path)})
    except Exception as exc:
        return _check(f"filesystem.{name}", CHECK_FAIL, f"目录不可写：{path}；{exc}", {"path": str(path)})


def _structure_export_check() -> dict[str, Any]:
    report = {
        "analysis_id": "health-check",
        "input": {"type": "smiles", "smiles": "CCO"},
        "ocsr": {"smiles": "CCO", "backend": "health"},
        "validation": {"canonical_smiles": "CCO", "standardized_smiles": "CCO"},
        "final": {"smiles": "CCO", "canonical_smiles": "CCO", "standardized_smiles": "CCO", "source": "health"},
    }
    try:
        mol = mol_text(report)
        sdf = sdf_text(report)
        ok = bool(mol.strip() and sdf.strip().endswith("$$$$"))
        return _check(
            "export.mol_sdf",
            CHECK_PASS if ok else CHECK_FAIL,
            "MOL/SDF 导出自检通过。" if ok else "MOL/SDF 导出内容异常。",
        )
    except Exception as exc:
        return _check("export.mol_sdf", CHECK_FAIL, f"MOL/SDF 导出自检失败：{exc}")


def _warmup_check(backend: str, runtime: Any, warmup_input: Path) -> dict[str, Any]:
    if not warmup_input.is_file():
        return _check("warmup", CHECK_FAIL, f"Warm-up 图片不存在：{warmup_input}", {"input": str(warmup_input)})
    started = time.perf_counter()
    try:
        result = runtime.recognize(warmup_input) if hasattr(runtime, "recognize") else _runtime_adapter(runtime).recognize(warmup_input)
        validation = validate_smiles(result.smiles)
        ok = result.status == "success" and bool(validation.get("valid"))
        return _check(
            "warmup",
            CHECK_PASS if ok else CHECK_FAIL,
            result.message if result.message else ("Warm-up 成功。" if ok else "Warm-up 未返回有效 SMILES。"),
            {
                "input": str(warmup_input),
                "status": result.status,
                "smiles": result.smiles,
                "canonical_smiles": validation.get("canonical_smiles"),
                "rdkit_valid": validation.get("valid"),
                "inference_time_ms": result.inference_time_ms,
                "total_warmup_time_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
    except Exception as exc:
        return _check(
            "warmup",
            CHECK_FAIL,
            f"Warm-up 推理失败：{exc}",
            {"input": str(warmup_input), "total_warmup_time_ms": round((time.perf_counter() - started) * 1000, 3)},
        )


def _primary_model_path(status: Mapping[str, Any]) -> Path | None:
    value = status.get("model_path")
    if value:
        return Path(str(value)).expanduser().resolve()
    for child in status.get("child_statuses") or []:
        if isinstance(child, Mapping) and child.get("model_path"):
            return Path(str(child["model_path"])).expanduser().resolve()
    return None


def _model_sha_from_status(status: Mapping[str, Any]) -> str | None:
    value = status.get("model_sha256")
    if value:
        return str(value)
    for child in status.get("child_statuses") or []:
        if isinstance(child, Mapping) and child.get("model_sha256"):
            return str(child["model_sha256"])
    return None


def _model_fingerprint_for_cache(backend: str) -> dict[str, Any] | None:
    paths: list[Path] = []
    if backend in {"molscribe", "ensemble"}:
        paths.append(Path(config.MOLSCRIBE_MODEL_PATH).expanduser().resolve())
    fingerprints = [_path_fingerprint(path) for path in paths]
    fingerprints = [item for item in fingerprints if item is not None]
    if not fingerprints:
        return None
    return {"files": fingerprints}


def _path_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.expanduser().resolve().stat()
    except OSError:
        return {"path": str(path.expanduser().resolve()), "exists": False}
    return {
        "path": str(path.expanduser().resolve()),
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def run_heavy_health_checks(
    backend: str,
    runtime_config: Mapping[str, Any] | None = None,
    *,
    production: bool = False,
    load_model: bool = False,
    warmup: bool = False,
    warmup_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run backend diagnostics that may import or load model frameworks."""
    started = time.perf_counter()
    runtime = dict(runtime_config or {})
    checks: list[dict[str, Any]] = []
    backend_runtime = None
    backend_status: dict[str, Any] = {"backend": backend, "available": False, "message": "Backend status was not collected."}
    warmup_check: dict[str, Any] | None = None
    model_load_count = 0
    adapter_reused_for_warmup = False
    try:
        backend_runtime = _build_health_runtime(backend, runtime)
        diagnose_load_model = bool(load_model and not warmup)
        backend_status = _backend_status_from_runtime(backend, backend_runtime, load_model=diagnose_load_model)
        if diagnose_load_model and _status_model_loaded(backend_status):
            model_load_count = 1
        if warmup:
            warmup_input = Path(warmup_path or config.PRODUCTION_HEALTH_WARMUP_INPUT).expanduser().resolve()
            _release_cached_child_adapters(backend_runtime)
            warmup_check = _warmup_check(backend, backend_runtime, warmup_input)
            adapter_reused_for_warmup = True
            refreshed_status = _backend_status_from_runtime(backend, backend_runtime, load_model=False)
            if not refreshed_status.get("exception"):
                backend_status = refreshed_status
            if _status_model_loaded(backend_status):
                model_load_count = max(model_load_count, 1)
        model_load_count = _reported_model_load_count(backend_runtime, backend_status, model_load_count)
    except Exception as exc:
        backend_status = {"backend": backend, "available": False, "message": str(exc), "exception": exc.__class__.__name__}
        if warmup:
            warmup_check = _check("warmup", CHECK_FAIL, f"Warm-up failed before inference started: {exc}")
    finally:
        _release_health_runtime(backend_runtime)
    checks.extend(_backend_checks(backend, backend_status, production))
    if warmup:
        if warmup_check is None:
            warmup_check = _check("warmup", CHECK_FAIL, "Warm-up failed before inference started.")
        checks.append(warmup_check)
    else:
        checks.append(_check("warmup", CHECK_SKIP, "Warm-up 未启用，本次未执行真实模型推理。"))
    return {
        "backend_status": backend_status,
        "checks": checks,
        "model_load_count": int(model_load_count),
        "adapter_reused_for_warmup": bool(adapter_reused_for_warmup),
        "peak_memory_available": False,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _run_backend_health(
    backend: str,
    runtime_config: Mapping[str, Any],
    *,
    production: bool,
    load_model: bool,
    warmup: bool,
    warmup_path: Path,
) -> dict[str, Any]:
    if backend == "demo":
        return run_heavy_health_checks(
            backend,
            runtime_config,
            production=production,
            load_model=False,
            warmup=False,
            warmup_path=warmup_path,
        )
    return _run_heavy_health_worker(
        backend,
        runtime_config,
        production=production,
        load_model=load_model,
        warmup=warmup,
        warmup_path=warmup_path,
    )


def _run_heavy_health_worker(
    backend: str,
    runtime_config: Mapping[str, Any],
    *,
    production: bool,
    load_model: bool,
    warmup: bool,
    warmup_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "src.runtime.health_worker",
        "--backend",
        backend,
        "--runtime-json",
        json.dumps(dict(runtime_config), ensure_ascii=False),
        "--warmup-input",
        str(warmup_path),
    ]
    if production:
        command.append("--production")
    if load_model:
        command.append("--load-model")
    if warmup:
        command.append("--warmup")
    env = os.environ.copy()
    env.setdefault("MOLSCRIBE_ISOLATED_SUBPROCESS", "true")
    env.setdefault("DECIMER_ISOLATED_SUBPROCESS", "true")
    timeout = max(
        120.0,
        float(config.OCSR_TIMEOUT_SECONDS) + 120.0,
        float(config.OCSR_ENSEMBLE_TOTAL_TIMEOUT_SECONDS) + 120.0,
    )
    result = run_json_command(
        command,
        cwd=config.PROJECT_ROOT,
        env=env,
        timeout=timeout,
        marker=HEALTH_WORKER_RESULT_MARKER,
    )
    worker = {
        "command": command,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "elapsed_ms": result.elapsed_ms,
        "stderr_tail": result.stderr.strip().splitlines()[-5:],
    }
    if result.payload and isinstance(result.payload, dict):
        result.payload["worker"] = worker
        return result.payload
    message = "健康检查子进程超时，已终止进程树。" if result.timed_out else (result.last_output_line() or "健康检查子进程未返回 JSON。")
    return {
        "backend_status": {"backend": backend, "available": False, "message": message},
        "checks": [_check("backend.worker", CHECK_FAIL, message, worker)],
        "model_load_count": 0,
        "adapter_reused_for_warmup": False,
        "peak_memory_available": False,
        "worker": worker,
    }


def _cache_key(
    backend: str,
    runtime_config: Mapping[str, Any],
    model_fingerprint: Mapping[str, Any] | None,
    load_model: bool,
    warmup: bool,
    warmup_path: Path,
) -> str:
    payload = {
        "health_probe_version": 2,
        "backend": backend,
        "runtime_config": dict(sorted((str(key), value) for key, value in runtime_config.items())),
        "model_fingerprint": model_fingerprint,
        "dependency_versions": dependency_versions(),
        "load_model": load_model,
        "warmup": warmup,
        "warmup_path": str(warmup_path),
        "warmup_input_fingerprint": _path_fingerprint(warmup_path),
        "app_mode": config.APP_MODE,
        "git_commit": git_commit(),
    }
    import hashlib

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _cache_path(cache_key: str) -> Path:
    return config.DATA_DIR / "health" / f"{cache_key}.json"


def _read_cached_health(cache_key: str, ttl_seconds: int) -> dict[str, Any] | None:
    if ttl_seconds <= 0:
        return None
    path = _cache_path(cache_key)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(payload.get("created_at")).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age > ttl_seconds:
            return None
        if payload.get("cache_key") != cache_key:
            return None
        return payload
    except Exception:
        return None


def _write_cached_health(payload: Mapping[str, Any]) -> None:
    try:
        path = _cache_path(str(payload["cache_key"]))
        ensure_directory(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception:
        return


def _repair_suggestions(backend: str, checks: list[dict[str, Any]], production: bool) -> list[str]:
    failed_names = {check["name"] for check in checks if check["status"] == CHECK_FAIL}
    suggestions: list[str] = []
    if "backend.policy" in failed_names:
        suggestions.append("生产模式请将 OCSR_BACKEND 设置为 molscribe、decimer 或 ensemble，或临时切回 APP_MODE=demo。")
    if "rdkit" in failed_names:
        suggestions.append("检查当前 Python 环境中的 RDKit 安装，SMILES 手动分析依赖它。")
    if any(name.startswith("filesystem.") for name in failed_names):
        suggestions.append("确认 data/outputs、data/runs 和文档输出目录存在且当前用户可写。")
    if "backend.package" in failed_names:
        suggestions.append(f"安装或修复 {backend} 后端依赖包，并在同一个虚拟环境中启动应用。")
    if "backend.model_file" in failed_names:
        suggestions.append("检查模型路径环境变量，例如 MOLSCRIBE_MODEL_PATH，确保文件存在且可读。")
    if "runtime.device" in failed_names:
        suggestions.append("核对 GPU/CPU 选择、CUDA_VISIBLE_DEVICES、PyTorch/TensorFlow 与驱动版本。")
    if "warmup" in failed_names:
        suggestions.append("先运行 scripts/check_ocsr_backend.py --production --warmup 查看完整 warm-up 错误。")
    if production:
        suggestions.append("健康检查失败时，SMILES 手动分析仍可用；图片、文档和批量真实识别会被禁用。")
    return list(dict.fromkeys(suggestions))


def _check(name: str, status: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": dict(details or {}),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def environment_health_snapshot(run_matrix_test: bool = False) -> dict[str, Any]:
    """Return framework and driver diagnostics for the health page."""
    return environment_status(run_matrix_test=run_matrix_test)
