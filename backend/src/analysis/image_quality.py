"""Lightweight image-quality signals for OCSR decision making."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

import config


def _score_range(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def assess_image_quality(image_path_or_array: str | Path | np.ndarray) -> dict[str, Any]:
    """Return simple, explainable image-quality diagnostics for an OCSR crop."""
    if isinstance(image_path_or_array, np.ndarray):
        image = image_path_or_array
    else:
        image = cv2.imdecode(np.fromfile(str(Path(image_path_or_array)), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return {
            "quality_score": 0.0,
            "passed": False,
            "reason_codes": ["image_unreadable"],
            "message": "图片无法读取。",
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    foreground = gray < 245
    ink_ratio = float(np.mean(foreground))
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    border = max(2, min(width, height) // 100)
    border_ink = 0.0
    if border > 0:
        border_pixels = np.concatenate(
            [
                foreground[:border, :].ravel(),
                foreground[-border:, :].ravel(),
                foreground[:, :border].ravel(),
                foreground[:, -border:].ravel(),
            ]
        )
        border_ink = float(np.mean(border_pixels))

    size_score = min(_score_range(min(width, height), 64, 180), _score_range(width * height, 80 * 80, 300 * 300))
    contrast_score = _score_range(contrast, 18, 120)
    blur_score = _score_range(blur_variance, 20, 900)
    if 0.006 <= ink_ratio <= 0.32:
        ink_score = 1.0
    elif ink_ratio < 0.006:
        ink_score = _score_range(ink_ratio, 0.001, 0.006)
    else:
        ink_score = max(0.0, 1.0 - _score_range(ink_ratio, 0.32, 0.65))
    crop_score = max(0.0, 1.0 - _score_range(border_ink, 0.05, 0.30))
    score = round(
        0.26 * size_score
        + 0.22 * contrast_score
        + 0.22 * blur_score
        + 0.20 * ink_score
        + 0.10 * crop_score,
        4,
    )

    reason_codes: list[str] = []
    if min(width, height) < 96:
        reason_codes.append("low_resolution")
    if contrast < 25:
        reason_codes.append("low_contrast")
    if blur_variance < 35:
        reason_codes.append("blurred")
    if ink_ratio < 0.006:
        reason_codes.append("too_little_foreground")
    if ink_ratio > 0.45:
        reason_codes.append("too_dense_foreground")
    if border_ink > 0.18:
        reason_codes.append("possibly_cropped")

    blocking_reasons = {"image_unreadable", "too_little_foreground"} & set(reason_codes)
    return {
        "width": int(width),
        "height": int(height),
        "pixel_count": int(width * height),
        "contrast": round(contrast, 3),
        "blur_variance": round(blur_variance, 3),
        "ink_ratio": round(ink_ratio, 5),
        "border_ink_ratio": round(border_ink, 5),
        "quality_score": score,
        "passed": bool(score >= config.DECISION_MIN_IMAGE_QUALITY and not blocking_reasons),
        "reason_codes": reason_codes,
    }
