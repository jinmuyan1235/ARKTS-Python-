"""DECIMER backend diagnostics and compatibility tests."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.ocsr.decimer_adapter import (
    DECIMERAdapter,
    DECIMERConfigurationError,
    DECIMERDependencyError,
    DECIMERInitializationError,
)


def _image(path: Path) -> Path:
    path.write_bytes(b"fake-image")
    return path


def test_decimer_missing_package_fails_helpfully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "DECIMER" else object())
    result = DECIMERAdapter().recognize(_image(tmp_path / "mol.png"))
    assert result.status == "failed"
    assert "未安装 DECIMER" in result.message
    assert result.inference_time_ms is not None


def test_decimer_import_error(monkeypatch, tmp_path: Path) -> None:
    adapter = DECIMERAdapter()
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_predictor", lambda: (_ for _ in ()).throw(DECIMERDependencyError("bad import")))
    result = adapter.recognize(_image(tmp_path / "mol.png"))
    assert result.status == "failed"
    assert "bad import" in result.message


def test_decimer_diagnose_reports_predictor_import_failure(monkeypatch) -> None:
    adapter = DECIMERAdapter()
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(
        adapter,
        "_tensorflow_status",
        lambda load=True: {
            "tensorflow_installed": True,
            "tensorflow_version": "2.test",
            "gpu_available": True,
            "detected_gpus": ["GPU:0"],
            "tensorflow": None,
        },
    )
    monkeypatch.setattr(
        adapter,
        "_import_predictor",
        lambda: (_ for _ in ()).throw(DECIMERDependencyError("keras mismatch")),
    )

    status = adapter.diagnose(load_model=True)

    assert status["initialization_success"] is False
    assert "keras mismatch" in status["load_error"]


def test_decimer_lightweight_diagnose_probes_tensorflow_gpu(monkeypatch) -> None:
    adapter = DECIMERAdapter(device="auto", strict_mode=False)
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    calls: list[bool] = []

    def fake_tensorflow_status(load: bool = True) -> dict:
        calls.append(load)
        return {
            "tensorflow_installed": True,
            "tensorflow_version": "2.test",
            "gpu_available": True if load else bool(adapter.detected_gpus),
            "detected_gpus": ["GPU:0"] if load else list(adapter.detected_gpus),
            "tensorflow": object() if load else None,
        }

    monkeypatch.setattr(adapter, "_tensorflow_status", fake_tensorflow_status)

    status = adapter.diagnose(load_model=False)

    assert calls[0] is True
    assert status["gpu_available"] is True
    assert status["device"] == "gpu"
    assert status["detected_gpus"] == ["GPU:0"]


def test_decimer_initialization_error(monkeypatch, tmp_path: Path) -> None:
    adapter = DECIMERAdapter()
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_resolve_device", lambda: (_ for _ in ()).throw(RuntimeError("init boom")))
    result = adapter.recognize(_image(tmp_path / "mol.png"))
    assert result.status == "failed"
    assert "初始化失败" in result.message
    assert "init boom" in result.message


def test_decimer_gpu_unavailable_never_silently_falls_back(monkeypatch) -> None:
    monkeypatch.setattr("src.ocsr.decimer_adapter.config.OCSR_GPU_REQUIRED", False)
    status = {
        "tensorflow_installed": True,
        "tensorflow_version": "2.test",
        "gpu_available": False,
        "detected_gpus": [],
        "tensorflow": None,
    }
    strict = DECIMERAdapter(device="gpu", strict_mode=False)
    monkeypatch.setattr(strict, "_tensorflow_status", lambda load=True: status)
    try:
        strict._resolve_device()
    except DECIMERConfigurationError as exc:
        assert "未检测到可用 GPU" in str(exc)
    else:
        raise AssertionError("Expected explicit GPU configuration failure")

    auto = DECIMERAdapter(device="auto", strict_mode=False)
    monkeypatch.setattr(auto, "_tensorflow_status", lambda load=True: status)
    auto._resolve_device()
    assert auto.device == "cpu"

    strict_auto = DECIMERAdapter(device="auto", strict_mode=True)
    monkeypatch.setattr(strict_auto, "_tensorflow_status", lambda load=True: status)
    try:
        strict_auto._resolve_device()
    except DECIMERConfigurationError as exc:
        assert "不允许回退 CPU" in str(exc)
    else:
        raise AssertionError("Expected strict auto device failure")


def test_decimer_success_string_result(monkeypatch, tmp_path: Path) -> None:
    adapter = DECIMERAdapter(model_version="unit")
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_predictor", lambda: lambda _image, confidence=True, hand_drawn=False: "CCO")
    result = adapter.recognize(_image(tmp_path / "mol.png"))
    assert result.status == "success"
    assert result.smiles == "CCO"
    assert result.confidence is None
    assert result.backend == "decimer"
    assert result.model_name == "DECIMER Image Transformer"
    assert result.model_version == "unit"
    assert result.inference_time_ms is not None


def test_decimer_invalid_smiles_is_failed_with_raw_output(monkeypatch, tmp_path: Path) -> None:
    adapter = DECIMERAdapter(model_version="unit")
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_predictor", lambda: lambda _image, confidence=True, hand_drawn=False: "C1(CC")

    result = adapter.recognize(_image(tmp_path / "invalid.png"))

    assert result.status == "failed"
    assert result.smiles is None
    assert result.raw_output == "C1(CC"
    assert "无法解析" in result.message


def test_decimer_return_format_compatibility() -> None:
    adapter = DECIMERAdapter()
    assert adapter._normalize_prediction({"smiles": "CCO", "confidence": 0.7}) == ("CCO", 0.7)
    assert adapter._normalize_prediction(["c1ccccc1", 0.8]) == ("c1ccccc1", 0.8)
    assert adapter._normalize_prediction(("CCN", [("C", 0.9)])) == ("CCN", None)
    assert adapter._normalize_prediction("CCCl") == ("CCCl", None)


def test_decimer_no_smiles_and_inference_exception(monkeypatch, tmp_path: Path) -> None:
    empty = DECIMERAdapter()
    empty.predictor = lambda _image, confidence=True, hand_drawn=False: {"confidence": 0.1}
    result = empty.recognize(_image(tmp_path / "empty.png"))
    assert result.status == "failed"
    assert "未返回 SMILES" in result.message

    raising = DECIMERAdapter()
    raising.predictor = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("predict boom"))
    result = raising.recognize(_image(tmp_path / "raising.png"))
    assert result.status == "failed"
    assert "predict boom" in result.message


def test_decimer_timeout(monkeypatch, tmp_path: Path) -> None:
    def slow(_image: Any, confidence: bool = True, hand_drawn: bool = False) -> str:
        time.sleep(0.05)
        return "CCO"

    adapter = DECIMERAdapter(timeout_seconds=0.001)
    adapter.predictor = slow
    result = adapter.recognize(_image(tmp_path / "slow.png"))
    assert result.status == "failed"
    assert "超时" in result.message
    assert result.inference_time_ms is not None


def test_decimer_numpy_input_and_strategy(monkeypatch) -> None:
    class Recorder:
        def __call__(self, image: np.ndarray, confidence: bool = True, hand_drawn: bool = False) -> str:
            assert image.shape == (8, 8, 3)
            return "CCO"

    adapter = DECIMERAdapter(image_strategy="binary")
    adapter.predictor = Recorder()
    result = adapter.recognize(np.ones((8, 8), dtype=np.uint8) * 255)
    assert result.status == "success"
    assert result.smiles == "CCO"


def test_decimer_status_and_diagnostics(monkeypatch) -> None:
    adapter = DECIMERAdapter()
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_predictor", lambda: lambda _image, confidence=True, hand_drawn=False: "CCO")
    status = adapter.diagnose(load_model=True)
    assert status["backend"] == "decimer"
    assert status["initialization_success"] is True
    assert "tensorflow_version" in status


def test_decimer_dependency_wrapped_as_initialization_error(monkeypatch, tmp_path: Path) -> None:
    adapter = DECIMERAdapter()
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_predictor", lambda: (_ for _ in ()).throw(RuntimeError("unexpected")))
    result = adapter.recognize(_image(tmp_path / "unexpected.png"))
    assert result.status == "failed"
    assert "初始化失败" in result.message
