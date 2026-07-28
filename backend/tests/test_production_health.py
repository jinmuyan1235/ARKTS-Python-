"""Tests for production startup health checks."""

from pathlib import Path

import pytest

from src.ocsr.base import BaseOCSRAdapter, OCSRResult
from src.ocsr.recognizer import MoleculeRecognizer
from src.runtime import health as health_module


def _patch_health_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(health_module.config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(health_module.config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(health_module.config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(health_module.config, "DOCUMENT_OUTPUT_DIR", tmp_path / "documents")


def test_production_health_blocks_demo_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_health_paths(monkeypatch, tmp_path)

    result = health_module.run_production_health_check(
        "demo",
        production=True,
        warmup=False,
        load_model=False,
        force=True,
        use_cache=False,
    )

    assert result["ready"] is False
    assert result["capabilities"]["smiles_manual"] is True
    assert result["capabilities"]["image_recognition"] is False
    assert any(check["name"] == "backend.policy" and check["status"] == "fail" for check in result["checks"])
    assert any("生产模式" in suggestion for suggestion in result["repair_suggestions"])


def test_ensemble_health_checks_child_packages_not_a_nonexistent_ensemble_package() -> None:
    checks = health_module._backend_checks(
        "ensemble",
        {
            "available": True,
            "message": "ensemble ready",
            "package_version": None,
            "device": "mixed",
            "child_statuses": [
                {"backend": "molscribe", "available": True, "package_installed": True},
                {"backend": "decimer", "available": True, "package_installed": True},
            ],
        },
        production=True,
    )

    package_check = next(check for check in checks if check["name"] == "backend.package")
    assert package_check["status"] == "pass"
    assert package_check["details"]["child_backends"] == ["molscribe", "decimer"]


def test_health_cache_reuses_successful_warmup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_health_paths(monkeypatch, tmp_path)
    warmup_image = tmp_path / "warmup.png"
    warmup_image.write_bytes(b"not-used-by-mock")
    calls = {"worker": 0}

    def fake_worker(*args, **kwargs):
        calls["worker"] += 1
        return {
            "backend_status": {
                "backend": "decimer",
                "available": True,
                "message": "ok",
                "package_installed": True,
                "package_version": "test",
                "model_loaded": bool(kwargs.get("load_model")),
                "device": "cpu",
            },
            "checks": [
                {"name": "backend.available", "status": "pass", "message": "ok", "details": {"backend": "decimer"}},
                {"name": "backend.package", "status": "pass", "message": "package ok", "details": {}},
                {"name": "backend.model_file", "status": "skip", "message": "no explicit model", "details": {}},
                {"name": "backend.model_load", "status": "pass", "message": "loaded", "details": {}},
                {"name": "runtime.device", "status": "pass", "message": "cpu", "details": {}},
                {"name": "warmup", "status": "pass", "message": "mock warm-up", "details": {"input": str(warmup_image)}},
            ],
            "worker": {"mock": True},
        }

    monkeypatch.setattr(health_module, "_run_heavy_health_worker", fake_worker)

    first = health_module.run_production_health_check(
        "decimer",
        production=True,
        warmup=True,
        load_model=True,
        warmup_input=warmup_image,
        force=False,
        use_cache=True,
        cache_ttl_seconds=3600,
    )
    second = health_module.run_production_health_check(
        "decimer",
        production=True,
        warmup=True,
        load_model=True,
        warmup_input=warmup_image,
        force=False,
        use_cache=True,
        cache_ttl_seconds=3600,
    )

    assert first["ready"] is True
    assert second["ready"] is True
    assert second["cached"] is True
    assert calls["worker"] == 1


def test_health_failure_disables_only_real_ocsr_workflows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_health_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        health_module,
        "_run_heavy_health_worker",
        lambda *args, **kwargs: {
            "backend_status": {
                "backend": "molscribe",
                "available": False,
                "message": "missing model",
                "package_installed": True,
                "model_loaded": False,
                "device": "cpu",
            },
            "checks": [
                {"name": "backend.available", "status": "fail", "message": "missing model", "details": {"backend": "molscribe"}},
                {"name": "backend.package", "status": "pass", "message": "package ok", "details": {}},
                {"name": "backend.model_file", "status": "skip", "message": "no explicit model", "details": {}},
                {"name": "backend.model_load", "status": "skip", "message": "not loaded", "details": {}},
                {"name": "runtime.device", "status": "pass", "message": "cpu", "details": {}},
                {"name": "warmup", "status": "skip", "message": "not enabled", "details": {}},
            ],
            "worker": {"mock": True},
        },
    )

    result = health_module.run_production_health_check(
        "molscribe",
        production=True,
        warmup=False,
        load_model=False,
        force=True,
        use_cache=False,
    )

    assert result["ready"] is False
    assert health_module.image_workflows_enabled(result) is False
    assert result["capabilities"]["smiles_manual"] is True
    assert result["capabilities"]["history"] is True
    assert any(check["name"] == "backend.available" for check in result["checks"])


def test_heavy_health_reuses_one_adapter_for_load_model_warmup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class CountingHealthAdapter(BaseOCSRAdapter):
        backend_name = "fake_health"
        instance_count = 0
        load_count = 0
        diagnose_load_requests: list[bool] = []

        def __init__(self) -> None:
            type(self).instance_count += 1
            self.loaded = False

        @property
        def health_model_load_count(self) -> int:
            return type(self).load_count

        def _load_model(self) -> None:
            if not self.loaded:
                type(self).load_count += 1
                self.loaded = True

        def diagnose(self, load_model: bool = False) -> dict[str, object]:
            type(self).diagnose_load_requests.append(bool(load_model))
            if load_model:
                self._load_model()
            return self.status()

        def status(self) -> dict[str, object]:
            return {
                "backend": self.backend_name,
                "available": True,
                "message": "ok",
                "package_installed": True,
                "package_version": "test",
                "model_loaded": self.loaded,
                "device": "cpu",
                "health_model_load_count": type(self).load_count,
            }

        def recognize(self, image_path_or_array: object) -> OCSRResult:
            self._load_model()
            return OCSRResult(
                smiles="CCO",
                confidence=0.99,
                backend=self.backend_name,
                status="success",
                message="fake warm-up",
                inference_time_ms=1.0,
            )

    monkeypatch.setitem(MoleculeRecognizer.ADAPTERS, "fake_health", CountingHealthAdapter)
    warmup_image = tmp_path / "warmup.png"
    warmup_image.write_bytes(b"fake image bytes")

    result = health_module.run_heavy_health_checks(
        "fake_health",
        production=True,
        load_model=True,
        warmup=True,
        warmup_path=warmup_image,
    )

    assert CountingHealthAdapter.instance_count == 1
    assert CountingHealthAdapter.load_count == 1
    assert CountingHealthAdapter.diagnose_load_requests == [False, False]
    assert result["model_load_count"] == 1
    assert result["adapter_reused_for_warmup"] is True
    assert result["peak_memory_available"] is False
    assert any(check["name"] == "backend.model_load" and check["status"] == "pass" for check in result["checks"])
    assert any(check["name"] == "warmup" and check["status"] == "pass" for check in result["checks"])


def test_health_cache_key_uses_model_fingerprint_not_full_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model = tmp_path / "model.pth"
    model.write_bytes(b"small model")
    monkeypatch.setattr(health_module.config, "MOLSCRIBE_MODEL_PATH", model)

    fingerprint = health_module._model_fingerprint_for_cache("molscribe")

    assert fingerprint == {
        "files": [
            {
                "path": str(model.resolve()),
                "exists": True,
                "size": model.stat().st_size,
                "mtime_ns": model.stat().st_mtime_ns,
            }
        ]
    }
