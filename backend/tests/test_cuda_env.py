"""Tests for CUDA library resolution order."""

from __future__ import annotations

from src.runtime import cuda_env


def test_ensure_cuda_library_path_prefers_virtualenv_libraries(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cuda_env,
        "nvidia_library_paths",
        lambda: ["/venv/nvidia/cudnn/lib", "/usr/lib/wsl/lib"],
    )
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/lib/wsl/lib:/system/lib")

    cuda_env.ensure_cuda_library_path()

    assert cuda_env.os.environ["LD_LIBRARY_PATH"].split(":") == [
        "/venv/nvidia/cudnn/lib",
        "/usr/lib/wsl/lib",
        "/system/lib",
    ]
