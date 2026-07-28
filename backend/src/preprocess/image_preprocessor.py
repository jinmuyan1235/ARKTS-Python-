"""OpenCV preprocessing pipeline for 2D molecular structure images."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from config import DEFAULT_IMAGE_SIZE
from .image_loader import load_image as read_image
from .user_adjustments import apply_user_adjustments


class ImagePreprocessor:
    """Apply denoising, thresholding, whitespace cropping and normalization."""

    def __init__(self, default_size: tuple[int, int] = DEFAULT_IMAGE_SIZE) -> None:
        self.default_size = default_size

    def load_image(self, path: Any) -> np.ndarray:
        """Load a supported image source into BGR format."""
        return read_image(path)

    @staticmethod
    def to_grayscale(image: np.ndarray) -> np.ndarray:
        """Convert BGR/BGRA input to grayscale, preserving grayscale input."""
        if image.ndim == 2:
            return image.copy()
        if image.ndim != 3:
            raise ValueError("图片数组维度无效。")
        if image.shape[2] == 1:
            return image[:, :, 0].copy()
        if image.shape[2] not in {3, 4}:
            raise ValueError("图片数组的通道数必须是 1、3 或 4。")
        conversion = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        return cv2.cvtColor(image, conversion)

    @staticmethod
    def denoise(image: np.ndarray) -> np.ndarray:
        """Remove light scan noise while retaining thin chemical bonds."""
        gray = ImagePreprocessor.to_grayscale(image)
        return cv2.fastNlMeansDenoising(gray, None, h=8, templateWindowSize=7, searchWindowSize=21)

    @staticmethod
    def binarize(image: np.ndarray) -> np.ndarray:
        """Create a black-foreground binary image using Otsu thresholding."""
        gray = ImagePreprocessor.to_grayscale(image)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if float(np.mean(binary)) < 127:
            binary = cv2.bitwise_not(binary)
        return binary

    @staticmethod
    def crop_whitespace(image: np.ndarray, padding: int = 12) -> np.ndarray:
        """Crop white borders around foreground strokes with a small safe margin."""
        gray = ImagePreprocessor.to_grayscale(image)
        coordinates = cv2.findNonZero((gray < 245).astype(np.uint8))
        if coordinates is None:
            return gray.copy()
        x, y, width, height = cv2.boundingRect(coordinates)
        x0, y0 = max(0, x - padding), max(0, y - padding)
        x1 = min(gray.shape[1], x + width + padding)
        y1 = min(gray.shape[0], y + height + padding)
        return gray[y0:y1, x0:x1].copy()

    @staticmethod
    def deskew(image: np.ndarray, max_angle: float = 15.0) -> np.ndarray:
        """Estimate and correct modest page rotation from foreground pixels."""
        gray = ImagePreprocessor.to_grayscale(image)
        points = np.column_stack(np.where(gray < 200))
        if len(points) < 20:
            return gray.copy()
        angle = cv2.minAreaRect(points[:, ::-1].astype(np.float32))[-1]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) > max_angle or abs(angle) < 0.2:
            return gray.copy()
        height, width = gray.shape
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        return cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=255)

    @staticmethod
    def resize_normalize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        """Resize with preserved aspect ratio and center on a white canvas."""
        if len(size) != 2 or min(size) <= 0:
            raise ValueError("归一化尺寸必须是两个正整数。")
        gray = ImagePreprocessor.to_grayscale(image)
        target_width, target_height = int(size[0]), int(size[1])
        height, width = gray.shape
        scale = min(target_width / max(width, 1), target_height / max(height, 1))
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        resized = cv2.resize(gray, (resized_width, resized_height), interpolation=interpolation)
        canvas = np.full((target_height, target_width), 255, dtype=np.uint8)
        x = (target_width - resized_width) // 2
        y = (target_height - resized_height) // 2
        canvas[y : y + resized_height, x : x + resized_width] = resized
        return canvas

    def preprocess_pipeline(self, image_path: Any, user_adjustments: dict[str, Any] | None = None) -> dict[str, np.ndarray]:
        """Run the complete CV pipeline and return every visualizable stage."""
        uploaded_original = self.load_image(image_path)
        original = apply_user_adjustments(uploaded_original, user_adjustments, self.default_size) if user_adjustments else uploaded_original
        clarity_enhanced = apply_user_adjustments(
            original,
            {"clarity_enhancement": "standard"},
            self.default_size,
        )
        gray = self.to_grayscale(original)
        denoised = self.denoise(gray)
        binary = self.binarize(denoised)
        cropped = self.crop_whitespace(binary)
        deskewed = self.deskew(cropped)
        normalized = self.resize_normalize(deskewed, self.default_size)
        stages = {
            "original": original,
            "clarity_enhanced": clarity_enhanced,
            "gray": gray,
            "denoised": denoised,
            "binary": binary,
            "cropped": cropped,
            "deskewed": deskewed,
            "normalized": normalized,
        }
        if user_adjustments:
            stages["uploaded_original"] = uploaded_original
            stages["user_adjusted"] = original
        return stages
