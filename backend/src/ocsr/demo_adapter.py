"""Filename-based fallback used when no real OCSR model is installed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BaseOCSRAdapter, OCSRResult


class DemoOCSRAdapter(BaseOCSRAdapter):
    """Recognize a small set of named samples for reliable demonstrations."""

    backend_name = "demo"
    preferred_image_stage = "original"
    SAMPLE_SMILES = {
        "aspirin": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
        "benzene": "c1ccccc1",
        "ethanol": "CCO",
    }

    def recognize(self, image_path_or_array: Any) -> OCSRResult:
        """Map a recognizable filename to a predefined demonstration SMILES."""
        if isinstance(image_path_or_array, (str, Path)):
            filename = self._matching_stem(Path(image_path_or_array))
            for keyword, smiles in self.SAMPLE_SMILES.items():
                if keyword in filename:
                    return OCSRResult(
                        smiles=smiles,
                        confidence=0.95,
                        backend=self.backend_name,
                        status="success",
                        message=f"演示模式根据文件名匹配到 {keyword}。",
                        model_name="demo-filename-map",
                        model_version="built-in",
                        device="cpu",
                        result_origin="demo_filename_map",
                    )
        return OCSRResult(
            smiles=None,
            confidence=None,
            backend=self.backend_name,
            status="failed",
            message=(
                "演示模式无法识别该文件名。请将样例命名为 aspirin、caffeine、benzene 或 ethanol，"
                "也可安装 MolScribe/DECIMER 或使用手动 SMILES 分析。"
            ),
            model_name="demo-filename-map",
            model_version="built-in",
            device="cpu",
            result_origin="demo_filename_map",
        )

    @staticmethod
    def _matching_stem(path: Path) -> str:
        stem = path.stem.lower()
        if stem != "original":
            return stem
        runtime_path = path.parent.parent / "runtime.json"
        if not runtime_path.is_file():
            return stem
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            original = str(runtime.get("original_filename") or "")
            return Path(original).stem.lower() or stem
        except Exception:
            return stem
