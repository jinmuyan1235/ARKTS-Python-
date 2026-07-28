"""Deterministic, ground-truth-independent OCSR input normalization profiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps


ProfileName = Literal[
    "raw", "alpha_flatten", "autocrop_and_pad", "scale_normalized",
    "contrast_normalized", "line_enhanced", "combined_normalized",
]


@dataclass(frozen=True)
class InputNormalizationConfig:
    profile: ProfileName = "raw"
    version: str = "ocsr-input-v1"
    target_size: int = 512
    foreground_threshold: int = 245
    crop_safety_px: int = 8
    white_border_ratio: float = 0.08
    contrast_cutoff_percent: float = 0.5
    line_enhance_radius: float = 0.7
    line_enhance_percent: int = 115
    line_enhance_threshold: int = 2
    output_mode: str = "RGB"

    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


PROFILE_CONFIGS: dict[str, InputNormalizationConfig] = {
    name: InputNormalizationConfig(profile=name)  # type: ignore[arg-type]
    for name in (
        "raw", "alpha_flatten", "autocrop_and_pad", "scale_normalized",
        "contrast_normalized", "line_enhanced", "combined_normalized",
    )
}


def get_profile(name: str, **overrides: object) -> InputNormalizationConfig:
    if name not in PROFILE_CONFIGS:
        raise ValueError(f"Unknown OCSR input profile: {name}")
    return replace(PROFILE_CONFIGS[name], **overrides)


def _open_flattened(source: str | Path | np.ndarray | Image.Image) -> Image.Image:
    if isinstance(source, Image.Image):
        image = source.copy()
    elif isinstance(source, np.ndarray):
        array = np.asarray(source)
        if array.ndim not in {2, 3}:
            raise ValueError(f"Unsupported image array dimensions: {array.ndim}")
        image = Image.fromarray(array.astype(np.uint8))
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"OCSR input image does not exist: {path}")
        with Image.open(path) as opened:
            image = opened.copy()
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _foreground_bbox(image: Image.Image, config: InputNormalizationConfig) -> tuple[int, int, int, int] | None:
    gray = np.asarray(image.convert("L"))
    mask = gray < config.foreground_threshold
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    left = max(0, int(xs.min()) - config.crop_safety_px)
    top = max(0, int(ys.min()) - config.crop_safety_px)
    right = min(image.width, int(xs.max()) + 1 + config.crop_safety_px)
    bottom = min(image.height, int(ys.max()) + 1 + config.crop_safety_px)
    return left, top, right, bottom


def _autocrop_and_pad(image: Image.Image, config: InputNormalizationConfig) -> Image.Image:
    bbox = _foreground_bbox(image, config)
    cropped = image.crop(bbox) if bbox else image.copy()
    border = max(config.crop_safety_px, int(round(max(cropped.size) * config.white_border_ratio)))
    return ImageOps.expand(cropped, border=border, fill="white")


def _scale(image: Image.Image, config: InputNormalizationConfig) -> Image.Image:
    target = max(64, int(config.target_size))
    border_budget = max(1, int(round(target * config.white_border_ratio)))
    fitted = ImageOps.contain(image, (target - 2 * border_budget, target - 2 * border_budget), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target, target), "white")
    canvas.paste(fitted, ((target - fitted.width) // 2, (target - fitted.height) // 2))
    return canvas


def _contrast(image: Image.Image, config: InputNormalizationConfig) -> Image.Image:
    return ImageOps.autocontrast(image.convert("L"), cutoff=config.contrast_cutoff_percent).convert("RGB")


def _line_enhance(image: Image.Image, config: InputNormalizationConfig) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(
        radius=config.line_enhance_radius,
        percent=config.line_enhance_percent,
        threshold=config.line_enhance_threshold,
    ))


def normalize_ocsr_input(
    source: str | Path | np.ndarray | Image.Image,
    config: InputNormalizationConfig | str = "raw",
) -> np.ndarray:
    """Return a new RGB array; the source image is never overwritten."""
    resolved = get_profile(config) if isinstance(config, str) else config
    image = _open_flattened(source)
    profile = resolved.profile
    if profile == "raw" or profile == "alpha_flatten":
        result = image
    elif profile == "autocrop_and_pad":
        result = _autocrop_and_pad(image, resolved)
    elif profile == "scale_normalized":
        result = _scale(image, resolved)
    elif profile == "contrast_normalized":
        result = _contrast(image, resolved)
    elif profile == "line_enhanced":
        result = _line_enhance(image, resolved)
    elif profile == "combined_normalized":
        result = _line_enhance(_contrast(_scale(_autocrop_and_pad(image, resolved), resolved), resolved), resolved)
    else:  # pragma: no cover - Literal plus get_profile prevents this.
        raise ValueError(f"Unsupported OCSR input profile: {profile}")
    return np.asarray(result.convert(resolved.output_mode)).copy()


def image_statistics(source: str | Path | np.ndarray | Image.Image, threshold: int = 245) -> dict[str, object]:
    """Compute renderer-neutral visual statistics without using structure truth."""
    original_mode = "array"
    has_alpha = False
    if isinstance(source, (str, Path)):
        with Image.open(Path(source)) as original:
            original_mode = original.mode
            has_alpha = original.mode in {"RGBA", "LA"} or "transparency" in original.info
    elif isinstance(source, Image.Image):
        original_mode = source.mode
        has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
    image = _open_flattened(source)
    gray = np.asarray(image.convert("L"))
    ink = gray < threshold
    ys, xs = np.where(ink)
    if len(xs):
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        foreground_occupancy = round(((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / gray.size, 6)
        border_whitespace = round(min(bbox[0], bbox[1], image.width - bbox[2], image.height - bbox[3]) / max(image.size), 6)
    else:
        bbox = []
        foreground_occupancy = 0.0
        border_whitespace = 1.0
    binary = ink.astype(np.uint8)
    component_count = int(max(0, cv2.connectedComponents(binary, connectivity=8)[0] - 1))
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    positive_distances = distance[distance > 0]
    thickness = round(float(np.median(positive_distances) * 2), 4) if positive_distances.size else 0.0
    return {
        "width": image.width, "height": image.height, "color_mode": original_mode,
        "alpha_channel": has_alpha, "foreground_bbox": json.dumps(bbox),
        "foreground_occupancy": foreground_occupancy, "ink_ratio": round(float(ink.mean()), 6),
        "contrast_std": round(float(gray.std()), 6), "connected_components": component_count,
        "border_whitespace_ratio": border_whitespace, "line_thickness_approx": thickness,
    }
