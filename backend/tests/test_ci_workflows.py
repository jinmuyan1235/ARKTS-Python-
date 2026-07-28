from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (PROJECT_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_regular_ci_matrix_compiles_and_runs_pytest_without_real_model_installers() -> None:
    source = _workflow("tests.yml")

    assert 'python-version: ["3.10", "3.11"]' in source
    assert "python -m compileall src scripts tests" in source
    assert "python -m pytest -q" in source
    assert "requirements-decimer.txt" not in source
    assert "download_ocsr_models.py" not in source
    assert "MolScribe" not in source
    assert "tensorflow" not in source.lower()


def test_manual_gpu_workflow_is_dispatch_only_and_uploads_diagnostics() -> None:
    source = _workflow("manual-gpu-ocsr.yml")

    assert "workflow_dispatch:" in source
    assert "pull_request:" not in source
    assert "push:" not in source
    assert "gpu_runner_label:" in source
    assert "- self-hosted" in source
    assert "- ${{ inputs.gpu_runner_label }}" in source
    assert "scripts/verify_gpu_environment.py --load-models --no-strict" in source
    assert "scripts/check_ocsr_backend.py" in source
    assert "--backend molscribe" in source
    assert "--backend decimer" in source
    assert "scripts/benchmark_gpu_inference.py --backend ensemble --limit 1" in source
    assert "actions/upload-artifact@v4" in source
    assert "gpu_environment.json" in source
    assert "molscribe_health.json" in source
    assert "decimer_health.json" in source
    assert "ensemble_smoke.json" in source
