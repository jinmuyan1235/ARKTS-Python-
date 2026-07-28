"""Real GPU integration checks, skipped when frameworks cannot see GPU."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.gpu
def test_pytorch_cuda_real_matrix_multiply() -> None:
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("PyTorch CUDA is not available in this environment.")
    assert torch.cuda.device_count() >= 1
    assert torch.cuda.get_device_name(0)
    tensor = torch.randn((512, 512), device="cuda:0")
    product = tensor @ tensor
    torch.cuda.synchronize()
    assert product.is_cuda


@pytest.mark.gpu
def test_tensorflow_gpu_real_matrix_multiply() -> None:
    from src.runtime.cuda_env import nvidia_library_paths

    env = os.environ.copy()
    paths = nvidia_library_paths()
    existing = [part for part in env.get("LD_LIBRARY_PATH", "").split(":") if part]
    env["LD_LIBRARY_PATH"] = ":".join([*paths, *existing])
    code = """
try:
    import tensorflow as tf
except Exception as exc:
    print(exc)
    raise SystemExit(2)
gpus = tf.config.list_physical_devices("GPU")
if not gpus:
    raise SystemExit(2)
with tf.device("/GPU:0"):
    tensor = tf.random.normal((512, 512))
    product = tf.matmul(tensor, tensor)
assert product.numpy().shape == (512, 512)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode == 2:
        pytest.skip(f"TensorFlow GPU is not available in this environment: {result.stdout}{result.stderr}")
    assert result.returncode == 0, result.stdout + result.stderr
