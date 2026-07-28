"""MolScribe backend diagnostics and compatibility tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np

from src.ocsr.molscribe_adapter import MolScribeAdapter, MolScribeConfigurationError


def test_molscribe_missing_package_fails_helpfully(monkeypatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "molscribe" else object())
    adapter = MolScribeAdapter(model_path="models/missing.pth")
    result = adapter.recognize("molecule.png")
    assert result.status == "failed"
    assert "未安装 MolScribe" in result.message
    assert result.backend == "molscribe"
    assert result.inference_time_ms is not None


def test_molscribe_missing_model_path(monkeypatch, tmp_path: Path) -> None:
    missing_model = tmp_path / "missing.pth"
    adapter = MolScribeAdapter(model_path=missing_model)
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    result = adapter.recognize("molecule.png")
    assert result.status == "failed"
    assert "模型文件不存在" in result.message
    assert result.model_name == missing_model.name


def test_molscribe_model_load_exception(monkeypatch, tmp_path: Path) -> None:
    model_path = tmp_path / "model.pth"
    model_path.write_bytes(b"placeholder")
    adapter = MolScribeAdapter(model_path=model_path)
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_molscribe_class", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = adapter.recognize("molecule.png")
    assert result.status == "failed"
    assert "boom" in result.message


def test_molscribe_mock_model_success_from_path(monkeypatch, tmp_path: Path) -> None:
    model_path = tmp_path / "model.pth"
    image_path = tmp_path / "molecule.png"
    model_path.write_bytes(b"placeholder")
    image_path.write_bytes(b"not-used-by-fake-model")

    class FakeModel:
        def predict_image_file(self, path: str, return_confidence: bool = True) -> dict[str, Any]:
            assert Path(path) == image_path
            assert return_confidence is True
            return {"smiles": "CCO", "confidence": "0.93"}

    adapter = MolScribeAdapter(model_path=model_path, model_version="test-version")
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_molscribe_class", lambda: lambda _path, device=None: FakeModel())
    result = adapter.recognize(image_path)
    assert result.status == "success"
    assert result.smiles == "CCO"
    assert result.confidence == 0.93
    assert result.inference_time_ms is not None
    assert result.model_version == "test-version"
    assert result.device == "cpu"


def test_molscribe_return_format_compatibility() -> None:
    adapter = MolScribeAdapter(model_path="models/model.pth")
    assert adapter._normalize_prediction({"predicted_smiles": "CCO", "score": 0.7}) == ("CCO", 0.7)
    assert adapter._normalize_prediction(("c1ccccc1", 0.8)) == ("c1ccccc1", 0.8)
    assert adapter._normalize_prediction("CCN") == ("CCN", None)


def test_molscribe_explicit_cuda_unavailable_does_not_fallback(monkeypatch) -> None:
    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

        @staticmethod
        def device(name: str) -> str:
            return name

    import sys

    monkeypatch.setitem(sys.modules, "torch", FakeTorch)
    adapter = MolScribeAdapter(model_path="models/model.pth", device="cuda:0")
    try:
        adapter._resolve_device_object()
    except MolScribeConfigurationError as exc:
        assert "不会静默回退 CPU" in str(exc)
    else:
        raise AssertionError("Expected explicit CUDA failure")


def test_molscribe_auto_can_fallback_unless_strict(monkeypatch) -> None:
    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

        @staticmethod
        def device(name: str) -> str:
            return name

    import sys

    monkeypatch.setitem(sys.modules, "torch", FakeTorch)
    monkeypatch.setattr("src.ocsr.molscribe_adapter.config.OCSR_GPU_REQUIRED", False)

    auto = MolScribeAdapter(model_path="models/model.pth", device="auto", strict_mode=False)
    assert auto._resolve_device_object() == "cpu"
    assert auto.device == "cpu"

    strict_auto = MolScribeAdapter(model_path="models/model.pth", device="auto", strict_mode=True)
    try:
        strict_auto._resolve_device_object()
    except MolScribeConfigurationError as exc:
        assert "不允许回退 CPU" in str(exc)
    else:
        raise AssertionError("Expected strict auto device failure")


def test_molscribe_numpy_input_uses_predict_image(monkeypatch, tmp_path: Path) -> None:
    model_path = tmp_path / "model.pth"
    model_path.write_bytes(b"placeholder")

    class FakeModel:
        def predict_image(self, image: np.ndarray, return_confidence: bool = True) -> tuple[str, float]:
            assert image.shape == (16, 16, 3)
            return "CCO", 0.5

    adapter = MolScribeAdapter(model_path=model_path, image_strategy="grayscale")
    monkeypatch.setattr(adapter, "_package_installed", lambda: True)
    monkeypatch.setattr(adapter, "_import_molscribe_class", lambda: lambda _path, device=None: FakeModel())
    result = adapter.recognize(np.ones((16, 16), dtype=np.uint8) * 255)
    assert result.status == "success"
    assert result.smiles == "CCO"
    assert result.inference_time_ms is not None
