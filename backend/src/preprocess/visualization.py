"""Visualization and persistence of preprocessing stages."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import cv2
import matplotlib.pyplot as plt
import numpy as np


STAGE_TITLES = {
    "original": "Original",
    "gray": "Grayscale",
    "denoised": "Denoised",
    "binary": "Binary",
    "cropped": "Cropped",
    "deskewed": "Deskewed",
    "normalized": "Normalized",
}


def save_preprocessing_stages(
    stages: Mapping[str, np.ndarray], output_dir: str | Path, prefix: str
) -> dict[str, str]:
    """Save all preprocessing stages and return their absolute paths."""
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, image in stages.items():
        path = destination / f"{prefix}_{name}.png"
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise RuntimeError(f"无法编码预处理阶段：{name}")
        encoded.tofile(path)
        paths[name] = str(path)
    return paths


def create_preprocessing_figure(
    stages: Mapping[str, np.ndarray], output_path: str | Path | None = None
) -> plt.Figure:
    """Create a compact matplotlib grid showing the CV pipeline."""
    items = [(name, stages[name]) for name in STAGE_TITLES if name in stages]
    columns = 4
    rows = (len(items) + columns - 1) // columns
    figure, axes = plt.subplots(rows, columns, figsize=(14, 3.5 * rows))
    axes_array = np.atleast_1d(axes).ravel()
    for axis, (name, image) in zip(axes_array, items):
        if name == "original" and image.ndim == 3:
            axis.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        else:
            axis.imshow(image, cmap="gray", vmin=0, vmax=255)
        axis.set_title(STAGE_TITLES[name])
        axis.axis("off")
    for axis in axes_array[len(items) :]:
        axis.axis("off")
    figure.tight_layout()
    if output_path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, dpi=150, bbox_inches="tight")
    return figure
