"""Single-GPU inference scheduling helpers."""

from __future__ import annotations

from contextlib import contextmanager
from threading import BoundedSemaphore
from typing import Iterator

import config


class InferenceScheduler:
    """Limit GPU model inference concurrency on a single local GPU."""

    def __init__(self, max_gpu_concurrent: int | None = None) -> None:
        limit = max_gpu_concurrent or config.OCSR_GPU_MAX_CONCURRENT_INFERENCE
        self.max_gpu_concurrent = max(1, int(limit))
        self._gpu_slots = BoundedSemaphore(self.max_gpu_concurrent)

    @contextmanager
    def gpu_slot(self, backend: str) -> Iterator[None]:
        acquired = self._gpu_slots.acquire(timeout=1)
        if not acquired:
            raise RuntimeError(f"{backend} GPU 推理队列繁忙，请稍后重试。")
        try:
            yield
        finally:
            self._gpu_slots.release()

    @contextmanager
    def slot_for_device(self, backend: str, device: str | None) -> Iterator[None]:
        if str(device or "").lower().startswith("cuda") or str(device or "").lower() == "gpu":
            with self.gpu_slot(backend):
                yield
        else:
            yield


GLOBAL_INFERENCE_SCHEDULER = InferenceScheduler()
