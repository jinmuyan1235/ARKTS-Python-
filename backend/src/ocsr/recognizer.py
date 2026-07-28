"""Backend selection and unified recognition error handling."""

from __future__ import annotations

from typing import Any

import config

from .base import BaseOCSRAdapter, OCSRResult
from .decimer_adapter import DECIMERAdapter
from .demo_adapter import DemoOCSRAdapter
from .ensemble import EnsembleOCSRAdapter
from .molscribe_adapter import MolScribeAdapter


class ProductionModeError(ValueError):
    """Raised when demo image recognition is requested in production mode."""


class MoleculeRecognizer:
    """Select and execute one of the configured OCSR adapters."""

    ADAPTERS: dict[str, type[BaseOCSRAdapter]] = {
        "demo": DemoOCSRAdapter,
        "molscribe": MolScribeAdapter,
        "decimer": DECIMERAdapter,
        "ensemble": EnsembleOCSRAdapter,
    }

    def __init__(self, backend: str | None = None, runtime_config: dict[str, Any] | None = None) -> None:
        self.backend = (backend or config.OCSR_BACKEND).strip().lower()
        if self.backend not in self.ADAPTERS:
            raise ValueError(f"Unsupported OCSR backend: {self.backend}. Choose demo/molscribe/decimer/ensemble.")
        if config.APP_MODE == "production" and self.backend == "demo":
            raise ProductionModeError("APP_MODE=production 禁止使用 demo 图片识别后端；请配置 MolScribe/DECIMER。")
        self.runtime_config = _runtime_config_with_defaults(runtime_config)
        self.adapter = self._build_adapter()

    def _build_adapter(self) -> BaseOCSRAdapter:
        adapter_class = self.ADAPTERS[self.backend]
        if self.backend == "molscribe" and adapter_class is MolScribeAdapter:
            return MolScribeAdapter(device=self.runtime_config.get("molscribe_device"))
        if self.backend == "decimer" and adapter_class is DECIMERAdapter:
            return DECIMERAdapter(
                device=self.runtime_config.get("decimer_device"),
                visible_gpu_index=self.runtime_config.get("visible_gpu_index"),
            )
        if self.backend == "ensemble" and adapter_class is EnsembleOCSRAdapter:
            return EnsembleOCSRAdapter(runtime_config=self.runtime_config)
        return adapter_class()

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        """Recognize an image and convert unexpected exceptions to failed results."""
        try:
            return self.adapter.recognize(image_path_or_array)
        except Exception as exc:
            return OCSRResult(None, None, self.backend, "failed", f"OCSR 未预期错误：{exc}")

    def status(self) -> dict[str, Any]:
        """Return readiness information for the selected adapter."""
        return self.adapter.status()

    @property
    def preferred_image_stage(self) -> str:
        """Return which preprocessing stage should be sent to the selected backend."""
        return getattr(self.adapter, "preferred_image_stage", "preprocessed")

    @property
    def is_demo(self) -> bool:
        """Return whether the recognizer is using the demonstration backend."""
        return self.backend == "demo"


def _runtime_config_with_defaults(runtime_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Use configured devices when non-UI entry points omit runtime_config."""
    runtime = dict(runtime_config or {})
    runtime.setdefault("molscribe_device", config.OCSR_DEVICE or "auto")
    runtime.setdefault("decimer_device", config.DECIMER_DEVICE or "auto")
    runtime.setdefault("visible_gpu_index", None)
    return runtime
