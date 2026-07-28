"""Tests for the OpenCV preprocessing pipeline."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src.preprocess.image_loader import load_image
from src.preprocess.image_preprocessor import ImagePreprocessor


def test_preprocessing_pipeline_does_not_crash(tmp_path: Path) -> None:
    image = np.full((220, 320, 3), 255, dtype=np.uint8)
    cv2.line(image, (50, 110), (270, 110), (0, 0, 0), 3)
    cv2.putText(image, "OH", (135, 95), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    image_path = tmp_path / "synthetic.png"
    success, encoded = cv2.imencode(".png", image)
    assert success
    encoded.tofile(image_path)

    result = ImagePreprocessor(default_size=(256, 256)).preprocess_pipeline(image_path)
    assert {"original", "clarity_enhanced", "gray", "denoised", "binary", "cropped", "normalized"} <= result.keys()
    assert result["normalized"].shape == (256, 256)
    assert result["binary"].dtype == np.uint8


def test_array_loader_normalizes_grayscale_and_rejects_bad_channels() -> None:
    gray = np.full((20, 30, 1), 127, dtype=np.float32)
    loaded = load_image(gray)
    assert loaded.shape == (20, 30, 3)
    assert loaded.dtype == np.uint8
    with pytest.raises(ValueError, match="通道数"):
        load_image(np.zeros((20, 30, 2), dtype=np.uint8))
