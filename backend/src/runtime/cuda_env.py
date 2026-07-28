"""CUDA shared-library path helpers for pip-installed NVIDIA wheels."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def nvidia_library_paths() -> list[str]:
    """Return existing NVIDIA library directories under the active Python prefix."""
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    root = Path(sys.prefix) / "lib" / version / "site-packages" / "nvidia"
    paths: list[str] = []
    for relative in (
        "cudnn/lib",
        "cublas/lib",
        "cuda_runtime/lib",
        "cuda_cupti/lib",
        "cuda_nvrtc/lib",
        "cufft/lib",
        "cufile/lib",
        "curand/lib",
        "cusolver/lib",
        "cusparse/lib",
        "cusparselt/lib",
        "nccl/lib",
        "nvjitlink/lib",
        "nvtx/lib",
        "nccl/lib",
    ):
        path = root / relative
        if path.is_dir():
            paths.append(str(path))
    # TensorFlow must resolve the cuDNN shipped by the active virtual
    # environment before it sees a possibly older WSL system copy.
    wsl_driver_path = Path("/usr/lib/wsl/lib")
    if wsl_driver_path.is_dir():
        paths.append(str(wsl_driver_path))
    return paths


def ensure_cuda_library_path(reexec: bool = False) -> None:
    """Prepend CUDA library paths, optionally restarting Python so dlopen can see them."""
    paths = nvidia_library_paths()
    if not paths:
        return
    existing = [part for part in os.environ.get("LD_LIBRARY_PATH", "").split(":") if part]
    remaining = [path for path in existing if path not in paths]
    desired = [*paths, *remaining]
    if desired == existing:
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join(desired)
    if reexec and os.name == "posix" and os.environ.get("MOLECULE_VISION_CUDA_REEXEC") != "1":
        os.environ["MOLECULE_VISION_CUDA_REEXEC"] = "1"
        os.execvpe(sys.executable, [sys.executable, *sys.argv], os.environ)
