"""Configuration loading should be validated and side-effect-light."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import config


def test_load_settings_falls_back_for_bad_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("OCSR_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("DOCUMENT_MAX_PAGES", "bad-int")
    monkeypatch.setenv("OCSR_GPU_MAX_CONCURRENT_INFERENCE", "0")
    monkeypatch.setenv("RUN_RETENTION_DAYS", "0")
    monkeypatch.setenv("RUN_MAX_STORAGE_GB", "bad-float")

    settings = config.load_settings()

    assert settings.ocsr_timeout_seconds == 120.0
    assert settings.document_max_pages == 100
    assert settings.document_max_regions == 500
    assert settings.document_max_file_size_mb == 100.0
    assert settings.ocsr_gpu_max_concurrent_inference == 1
    assert settings.run_retention_days == 1
    assert settings.run_max_storage_gb == 10.0


def test_app_mode_loads_demo_or_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "production")
    assert config.load_settings().app_mode == "production"
    monkeypatch.setenv("APP_MODE", "unexpected")
    assert config.load_settings().app_mode == "demo"


def test_production_requires_calibrated_confidence_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_REQUIRE_CALIBRATED_CONFIDENCE", raising=False)
    monkeypatch.setenv("APP_MODE", "production")
    assert config.load_settings().decision_require_calibrated_confidence is True

    monkeypatch.setenv("APP_MODE", "demo")
    assert config.load_settings().decision_require_calibrated_confidence is False

    monkeypatch.setenv("APP_MODE", "production")
    monkeypatch.setenv("DECISION_REQUIRE_CALIBRATED_CONFIDENCE", "false")
    assert config.load_settings().decision_require_calibrated_confidence is False


def test_default_fallback_image_strategy_order(monkeypatch) -> None:
    monkeypatch.delenv("OCSR_FALLBACK_IMAGE_STRATEGIES", raising=False)
    assert config.load_settings().ocsr_fallback_image_strategies == (
        "original",
        "enhanced",
        "normalized",
        "grayscale",
        "binary",
    )


def test_production_health_settings_load_from_env(monkeypatch, tmp_path: Path) -> None:
    warmup = tmp_path / "warmup.png"
    monkeypatch.setenv("PRODUCTION_HEALTH_CACHE_ENABLED", "false")
    monkeypatch.setenv("PRODUCTION_HEALTH_LOAD_MODEL", "false")
    monkeypatch.setenv("PRODUCTION_HEALTH_WARMUP", "false")
    monkeypatch.setenv("PRODUCTION_HEALTH_CACHE_TTL_SECONDS", "5")
    monkeypatch.setenv("PRODUCTION_HEALTH_WARMUP_INPUT", str(warmup))

    settings = config.load_settings()

    assert settings.production_health_cache_enabled is False
    assert settings.production_health_load_model is False
    assert settings.production_health_warmup is False
    assert settings.production_health_cache_ttl_seconds == 5
    assert settings.production_health_warmup_input == warmup.resolve()


def test_import_config_does_not_create_document_output_dir(tmp_path: Path) -> None:
    target = tmp_path / "not_created_on_import"
    code = "import config, os; print(config.DOCUMENT_OUTPUT_DIR)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={**dict(os.environ), "DOCUMENT_OUTPUT_DIR": str(target)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_initialize_directories_is_explicit(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "documents"
    runs = tmp_path / "runs"
    monkeypatch.setenv("DOCUMENT_OUTPUT_DIR", str(target))
    monkeypatch.setenv("RUNS_DIR", str(runs))
    settings = config.load_settings()

    config.initialize_directories(settings)

    assert target.is_dir()
    assert runs.is_dir()
