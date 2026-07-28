"""Production-oriented, optional MolScribe backend adapter."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from PIL import Image

import config
from src.chem.smiles_validator import validate_smiles
from src.runtime.cuda_env import nvidia_library_paths
from src.runtime.inference_scheduler import GLOBAL_INFERENCE_SCHEDULER
from src.runtime.job_manager import MODEL_WORKER_RESULT_MARKER, run_json_command
from src.runtime.metadata import dependency_versions, git_commit, sha256_file
from .base import BaseOCSRAdapter, OCSRResult

ImageStrategy = Literal["original", "grayscale", "normalized", "binary"]


class MolScribeAdapterError(RuntimeError):
    """Base class for classified MolScribe adapter errors."""


class MolScribeDependencyError(MolScribeAdapterError):
    """Raised when the optional MolScribe package is unavailable."""


class MolScribeConfigurationError(MolScribeAdapterError):
    """Raised when model path or device configuration is invalid."""


class MolScribeModelLoadError(MolScribeAdapterError):
    """Raised when the MolScribe model cannot be constructed."""


class MolScribeInferenceError(MolScribeAdapterError):
    """Raised when MolScribe inference fails or returns no SMILES."""


class MolScribeAdapter(BaseOCSRAdapter):
    """Wrap MolScribe without requiring it for demo, RDKit or Streamlit startup."""

    backend_name = "molscribe"

    def __init__(
        self,
        model_path: str | Path | None = None,
        device: str | None = None,
        timeout_seconds: float | None = None,
        image_strategy: ImageStrategy | None = None,
        strict_mode: bool | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        isolated_subprocess: bool | None = None,
    ) -> None:
        self.model_path = self._coerce_model_path(model_path)
        self.device = (device or "cpu").strip().lower()
        self.timeout_seconds = float(timeout_seconds or config.OCSR_TIMEOUT_SECONDS)
        self.image_strategy: ImageStrategy = image_strategy or config.MOLSCRIBE_IMAGE_STRATEGY
        self.strict_mode = config.OCSR_STRICT_MODE if strict_mode is None else strict_mode
        self.model_name = model_name or (self.model_path.name if model_path is not None else config.MOLSCRIBE_MODEL_NAME)
        self.model_version = model_version or config.MOLSCRIBE_MODEL_VERSION
        self.isolated_subprocess = (
            config.MOLSCRIBE_ISOLATED_SUBPROCESS if isolated_subprocess is None else isolated_subprocess
        )
        self.package_version = self._detect_package_version()
        self.model: Any | None = None
        self._load_error: str | None = None
        self._device_object: Any | None = None
        self.last_inference_time_ms: float | None = None
        self.preferred_image_stage = "preprocessed" if config.OCSR_USE_PREPROCESSED_IMAGE else "original"

    @staticmethod
    def _coerce_model_path(model_path: str | Path | None) -> Path:
        path = Path(model_path).expanduser() if model_path is not None else config.MOLSCRIBE_MODEL_PATH
        return (path if path.is_absolute() else config.PROJECT_ROOT / path).resolve()

    @staticmethod
    def _detect_package_version() -> str | None:
        for package_name in ("molscribe", "MolScribe"):
            try:
                return importlib.metadata.version(package_name)
            except importlib.metadata.PackageNotFoundError:
                continue
        return None

    @staticmethod
    def _package_installed() -> bool:
        return importlib.util.find_spec("molscribe") is not None

    def _import_molscribe_class(self) -> type[Any]:
        if not self._package_installed():
            raise MolScribeDependencyError("未安装 MolScribe。请先按 README 安装可选真实 OCSR 后端。")
        try:
            module = importlib.import_module("molscribe")
            return getattr(module, "MolScribe")
        except (ImportError, AttributeError) as exc:
            raise MolScribeDependencyError(f"MolScribe 包已发现，但无法导入 MolScribe 类：{exc}") from exc

    def _resolve_device_object(self) -> Any:
        requested = self.device
        try:
            import torch
        except Exception as exc:
            if requested.startswith("cuda") or requested == "gpu":
                raise MolScribeConfigurationError(f"请求 CUDA 设备，但 PyTorch 不可导入：{exc}") from exc
            if requested == "auto" and (self.strict_mode or config.OCSR_GPU_REQUIRED):
                raise MolScribeConfigurationError(f"自动选择设备时 PyTorch 不可导入，且当前配置不允许回退 CPU：{exc}") from exc
            self.device = "cpu"
            return "cpu"
        wants_cuda = requested == "auto" or requested == "gpu" or requested.startswith("cuda")
        if wants_cuda and torch.cuda.is_available():
            self.device = "cuda" if requested in {"auto", "gpu"} else requested
            return torch.device(self.device)
        if requested == "auto":
            if self.strict_mode or config.OCSR_GPU_REQUIRED:
                raise MolScribeConfigurationError("自动选择设备时 CUDA 不可用，且当前配置不允许回退 CPU。")
            self.device = "cpu"
            return torch.device("cpu")
        if requested == "gpu" or requested.startswith("cuda"):
            raise MolScribeConfigurationError("请求 CUDA 设备，但 torch.cuda.is_available() 为 False；不会静默回退 CPU。")
        self.device = "cpu"
        return torch.device("cpu")

    def _load_model(self) -> Any:
        if self.model is not None:
            return self.model
        if not self._package_installed():
            raise MolScribeDependencyError("未安装 MolScribe。请先按 README 安装可选真实 OCSR 后端。")
        if not self.model_path.is_file():
            raise MolScribeConfigurationError(f"MolScribe 模型文件不存在：{self.model_path}")
        molscribe_class = self._import_molscribe_class()
        try:
            self._device_object = self._resolve_device_object()
            self.model = molscribe_class(str(self.model_path), device=self._device_object)
            self._load_error = None
            return self.model
        except MolScribeAdapterError:
            raise
        except Exception as exc:
            self._load_error = str(exc)
            raise MolScribeModelLoadError(f"MolScribe 模型加载失败：{exc}") from exc

    def _result(
        self,
        smiles: str | None,
        confidence: float | None,
        status: Literal["success", "failed"],
        message: str,
        inference_time_ms: float | None = None,
        raw_output: str | None = None,
        failure_category: str | None = None,
        exception: BaseException | None = None,
    ) -> OCSRResult:
        return OCSRResult(
            smiles=smiles,
            confidence=confidence,
            backend=self.backend_name,
            status=status,
            message=message,
            inference_time_ms=inference_time_ms,
            model_name=self.model_name,
            model_version=self.model_version,
            model_sha256=sha256_file(self.model_path),
            device=self.device,
            package_version=self.package_version,
            git_commit=git_commit(),
            dependency_versions=dependency_versions(),
            result_origin="real_model",
            raw_output=raw_output,
            failure_category=failure_category,
            exception_type=type(exception).__name__ if exception else None,
            exception_summary=str(exception) if exception else None,
        )

    @staticmethod
    def _normalize_confidence(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_prediction(self, prediction: Any) -> tuple[str | None, float | None]:
        if isinstance(prediction, dict):
            smiles = (
                prediction.get("smiles")
                or prediction.get("SMILES")
                or prediction.get("predicted_smiles")
                or prediction.get("pred")
            )
            confidence = (
                prediction.get("confidence")
                or prediction.get("score")
                or prediction.get("probability")
                or prediction.get("confidence_score")
            )
            return (str(smiles).strip() if smiles else None, self._normalize_confidence(confidence))
        if isinstance(prediction, (tuple, list)):
            if prediction and isinstance(prediction[0], dict):
                return self._normalize_prediction(prediction[0])
            smiles = prediction[0] if prediction else None
            confidence = prediction[1] if len(prediction) > 1 else None
            return (str(smiles).strip() if smiles else None, self._normalize_confidence(confidence))
        smiles = getattr(prediction, "smiles", None) or getattr(prediction, "SMILES", None)
        confidence = getattr(prediction, "confidence", None)
        if smiles:
            return str(smiles).strip(), self._normalize_confidence(confidence)
        if isinstance(prediction, str):
            return prediction.strip(), None
        return None, None

    @staticmethod
    def _load_array_from_path(path: Path) -> np.ndarray:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"))

    @staticmethod
    def _normalize_array(image: Any) -> np.ndarray:
        array = np.asarray(image)
        if array.ndim not in {2, 3}:
            raise MolScribeInferenceError(f"不支持的图像数组维度：{array.ndim}")
        if array.dtype != np.uint8:
            clipped = np.clip(array, 0, 255)
            if clipped.max(initial=0) <= 1:
                clipped = clipped * 255
            array = clipped.astype(np.uint8)
        if array.ndim == 2:
            array = np.stack([array] * 3, axis=-1)
        if array.shape[-1] == 4:
            array = array[..., :3]
        return array

    def _prepare_image_array(self, image_path_or_array: Any) -> np.ndarray:
        if isinstance(image_path_or_array, (str, Path)):
            path = Path(image_path_or_array).expanduser().resolve()
            if not path.is_file():
                raise MolScribeInferenceError(f"输入图片不存在：{path}")
            array = self._load_array_from_path(path)
        else:
            array = self._normalize_array(image_path_or_array)

        if self.image_strategy == "original":
            return self._normalize_array(array)
        gray = np.asarray(Image.fromarray(self._normalize_array(array)).convert("L"))
        if self.image_strategy == "grayscale":
            return np.stack([gray] * 3, axis=-1)
        if self.image_strategy == "binary":
            threshold = int(gray.mean())
            binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
            return np.stack([binary] * 3, axis=-1)
        normalized = gray.astype(np.float32)
        minimum = float(normalized.min(initial=0))
        maximum = float(normalized.max(initial=255))
        if maximum > minimum:
            normalized = (normalized - minimum) * (255.0 / (maximum - minimum))
        return np.stack([normalized.astype(np.uint8)] * 3, axis=-1)

    def _predict_with_model(self, model: Any, image_path_or_array: Any) -> Any:
        if isinstance(image_path_or_array, (str, Path)) and self.image_strategy == "original":
            path = Path(image_path_or_array).expanduser().resolve()
            if not path.is_file():
                raise MolScribeInferenceError(f"输入图片不存在：{path}")
            if hasattr(model, "predict_image_file"):
                try:
                    return model.predict_image_file(str(path), return_confidence=True)
                except TypeError:
                    return model.predict_image_file(str(path))

        image_array = self._prepare_image_array(image_path_or_array)
        if hasattr(model, "predict_image"):
            try:
                return model.predict_image(image_array, return_confidence=True)
            except TypeError:
                return model.predict_image(image_array)
        if isinstance(image_path_or_array, (str, Path)) and hasattr(model, "predict_image_file"):
            return model.predict_image_file(str(Path(image_path_or_array).expanduser().resolve()))
        raise MolScribeInferenceError("当前 MolScribe 模型对象不支持 predict_image_file 或 predict_image。")

    def _run_with_timeout(self, function: Callable[[], Any]) -> Any:
        if self.timeout_seconds <= 0:
            return function()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(function)
        try:
            return future.result(timeout=self.timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise MolScribeInferenceError(f"MolScribe 推理超过 {self.timeout_seconds:.1f} 秒超时。") from exc
        finally:
            if future.done():
                executor.shutdown(wait=True)

    def _path_for_subprocess(self, image_path_or_array: Any) -> tuple[str, Path | None]:
        if isinstance(image_path_or_array, (str, Path)):
            path = Path(image_path_or_array).expanduser().resolve()
            if not path.is_file():
                raise MolScribeInferenceError(f"输入图片不存在：{path}")
            return str(path), None
        array = self._normalize_array(image_path_or_array)
        handle = tempfile.NamedTemporaryFile(prefix="molscribe_input_", suffix=".png", delete=False)
        handle.close()
        temp_path = Path(handle.name)
        Image.fromarray(array).save(temp_path)
        return str(temp_path), temp_path

    def _subprocess_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        paths = nvidia_library_paths()
        existing = [part for part in env.get("LD_LIBRARY_PATH", "").split(":") if part]
        if paths:
            env["LD_LIBRARY_PATH"] = ":".join([*paths, *existing])
        env["MOLSCRIBE_ISOLATED_SUBPROCESS"] = "false"
        env["MOLSCRIBE_CHILD_PROCESS"] = "1"
        env["MOLSCRIBE_DEVICE"] = self.device
        env["OCSR_DEVICE"] = self.device
        env["MOLSCRIBE_MODEL_PATH"] = str(self.model_path)
        env["MOLSCRIBE_IMAGE_STRATEGY"] = self.image_strategy
        env["OCSR_TIMEOUT_SECONDS"] = str(self.timeout_seconds)
        env["OCSR_STRICT_MODE"] = "true" if self.strict_mode else "false"
        return env

    def _recognize_in_subprocess(self, image_path_or_array: Any) -> OCSRResult:
        start = time.perf_counter()
        image_path, temp_path = self._path_for_subprocess(image_path_or_array)
        try:
            completed = run_json_command(
                [
                    sys.executable,
                    "-m",
                    "src.runtime.model_worker",
                    "--backend",
                    "molscribe",
                    "--input",
                    image_path,
                    "--device",
                    self.device,
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=self._subprocess_environment(),
                timeout=max(self.timeout_seconds + 60, 120),
                marker=MODEL_WORKER_RESULT_MARKER,
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        self.last_inference_time_ms = elapsed_ms
        if completed.timed_out:
            return self._result(
                None, None, "failed", f"MolScribe 隔离子进程超过 {max(self.timeout_seconds + 60, 120):.1f} 秒超时。",
                elapsed_ms, failure_category="timeout",
            )
        if completed.returncode != 0 or completed.payload is None:
            message = completed.last_output_line() or f"子进程退出码 {completed.returncode}"
            return self._result(
                None, None, "failed", f"MolScribe 隔离子进程失败：{message}",
                elapsed_ms, failure_category="subprocess_failure",
            )
        data = completed.payload
        result = OCSRResult(**{key: data.get(key) for key in OCSRResult.__dataclass_fields__})
        result.inference_time_ms = elapsed_ms
        self.device = result.device or self.device
        return result

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        """Run MolScribe inference and return a diagnostic-rich normalized result."""
        if (
            self.isolated_subprocess
            and os.environ.get("MOLSCRIBE_CHILD_PROCESS") != "1"
            and "PYTEST_CURRENT_TEST" not in os.environ
        ):
            return self._recognize_in_subprocess(image_path_or_array)
        start = time.perf_counter()
        try:
            model = self._load_model()
            with GLOBAL_INFERENCE_SCHEDULER.slot_for_device(self.backend_name, self.device):
                prediction = self._run_with_timeout(lambda: self._predict_with_model(model, image_path_or_array))
            smiles, confidence = self._normalize_prediction(prediction)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            self.last_inference_time_ms = elapsed_ms
            raw_output = smiles.strip() if isinstance(smiles, str) else None
            if raw_output:
                validation = validate_smiles(raw_output)
                if not validation["valid"]:
                    return self._result(
                        None,
                        confidence,
                        "failed",
                        "MolScribe 返回了无法解析的结构字符串，请调整区域或使用人工修正。",
                        elapsed_ms,
                        raw_output=raw_output,
                        failure_category="output_parse_failure",
                    )
                return self._result(raw_output, confidence, "success", "MolScribe 识别完成。", elapsed_ms, raw_output=raw_output)
            if not smiles:
                raise MolScribeInferenceError("MolScribe 未返回 SMILES。")
            return self._result(smiles, confidence, "success", "MolScribe 识别完成。", elapsed_ms)
        except MolScribeAdapterError as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            self.last_inference_time_ms = elapsed_ms
            category = "timeout" if "timeout" in str(exc).lower() else (
                "dependency_failure" if isinstance(exc, MolScribeModelLoadError) else "model_inference_failure"
            )
            return self._result(None, None, "failed", str(exc), elapsed_ms, failure_category=category, exception=exc)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
            self.last_inference_time_ms = elapsed_ms
            return self._result(
                None, None, "failed", f"MolScribe 推理失败：{exc}", elapsed_ms,
                failure_category="model_inference_failure", exception=exc,
            )

    @property
    def is_available(self) -> bool:
        """Return whether MolScribe appears configured for lazy model loading."""
        return self._package_installed() and self.model_path.is_file() and self._load_error is None

    @property
    def availability_message(self) -> str:
        """Describe the current MolScribe configuration state without importing torch/model weights."""
        if not self._package_installed():
            return "未安装 MolScribe。demo、手动 SMILES 和 RDKit 分析仍可正常使用。"
        if not self.model_path.is_file():
            return f"MolScribe 模型文件不存在：{self.model_path}"
        if self._load_error:
            return f"MolScribe 模型加载失败：{self._load_error}"
        if self.model is None:
            return "MolScribe 已配置；模型将在第一次真实识别时延迟加载。"
        return "MolScribe 模型已加载。"

    def status(self) -> dict[str, Any]:
        """Return JSON-friendly backend diagnostics for Streamlit and scripts."""
        return {
            "backend": self.backend_name,
            "available": self.is_available,
            "message": self.availability_message,
            "package_installed": self._package_installed(),
            "package_version": self.package_version,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.is_file(),
            "model_loaded": self.model is not None,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "device": self.device,
            "image_strategy": self.image_strategy,
            "timeout_seconds": self.timeout_seconds,
            "strict_mode": self.strict_mode,
            "isolated_subprocess": self.isolated_subprocess,
            "last_inference_time_ms": self.last_inference_time_ms,
            "torch": {
                "installed": importlib.util.find_spec("torch") is not None,
                "cuda_available": None,
                "note": "UI 状态页不导入 PyTorch；请用 scripts/verify_gpu_environment.py 验证 CUDA。",
            },
        }

    def diagnose(self, load_model: bool = False) -> dict[str, Any]:
        """Return detailed diagnostics, optionally attempting model construction."""
        diagnostics = self.status()
        try:
            from src.runtime.gpu_manager import torch_status
            import torch

            diagnostics["torch"] = torch_status(run_matrix_test=False)
            diagnostics["cuda_available"] = bool(torch.cuda.is_available())
        except Exception as exc:
            diagnostics["cuda_available"] = False
            diagnostics["torch_error"] = str(exc)
        if load_model:
            try:
                self._load_model()
                diagnostics.update(self.status())
                diagnostics["model_loaded"] = True
            except MolScribeAdapterError as exc:
                diagnostics.update(self.status())
                diagnostics["model_loaded"] = False
                diagnostics["load_error"] = str(exc)
        return diagnostics
