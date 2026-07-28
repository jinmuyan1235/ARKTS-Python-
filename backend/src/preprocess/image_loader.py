"""Robust image loading for paths, bytes, PIL images and NumPy arrays."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from config import SUPPORTED_IMAGE_EXTENSIONS


def load_image(source: Any) -> np.ndarray:
    """Load an image as a BGR NumPy array and raise readable errors."""
    if isinstance(source, np.ndarray):
        if source.size == 0:
            raise ValueError("图片数组为空。")
        if source.ndim not in {2, 3}:
            raise ValueError("图片数组必须是二维灰度图或三维彩色图。")
        if source.ndim == 3 and source.shape[2] not in {1, 3, 4}:
            raise ValueError("彩色图片数组的通道数必须是 1、3 或 4。")
        if np.issubdtype(source.dtype, np.floating) and not np.isfinite(source).all():
            raise ValueError("图片数组不能包含 NaN 或无穷值。")
        if np.issubdtype(source.dtype, np.floating) and float(np.max(source)) <= 1.0 and float(np.min(source)) >= 0.0:
            image = np.rint(source * 255).astype(np.uint8)
        else:
            image = np.clip(source, 0, 255).astype(np.uint8)
        if source.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if source.shape[2] == 1:
            return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
        return image.copy()
    if isinstance(source, Image.Image):
        rgb = np.asarray(source.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if isinstance(source, (bytes, bytearray)):
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("无法解码上传的图片数据。")
        return image
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在：{path}")
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"不支持的图片格式：{path.suffix}。仅支持 PNG/JPG/JPEG。")
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"图片损坏或无法读取：{path}")
    return image


class ImageLoader:
    """Object-oriented facade for image loading and format checks."""

    @staticmethod
    def load(source: Any) -> np.ndarray:
        """Load a supported source as a BGR image."""
        return load_image(source)
