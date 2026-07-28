"""Tests for lightweight user-controlled single-image preprocessing."""

from pathlib import Path

import cv2
import numpy as np

from src.preprocess.image_preprocessor import ImagePreprocessor
from src.preprocess.user_adjustments import (
    apply_user_adjustments,
    has_user_adjustments,
    normalize_user_adjustments,
    save_user_adjusted_image,
)
from src.runtime.run_store import create_image_run_from_bytes
from src.ui.image_editor import (
    _consume_crop_click_from_params,
    _prepare_crop_state_context,
    _prepare_crop_state_values,
    crop_bbox_from_points,
    image_identity_from_bytes,
)
from src.ui.image_page import _attach_user_preprocessing, _prepare_effective_input


def _synthetic_structure() -> np.ndarray:
    image = np.full((80, 120, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 40), (100, 40), (0, 0, 0), 3)
    cv2.putText(image, "OH", (48, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
    return image


def _encode_png(image: np.ndarray) -> bytes:
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()


def test_normalize_user_adjustments_clamps_crop_and_values() -> None:
    image = _synthetic_structure()

    normalized = normalize_user_adjustments(
        {
            "crop_bbox": [-10, 5, 90, 999],
            "rotation": "not-a-number",
            "contrast": 9,
            "clarity_enhancement": "strong",
            "output_stage": "unsupported",
        },
        image.shape,
    )

    assert normalized["crop_bbox"] == [0, 5, 90, 80]
    assert normalized["rotation"] == 0
    assert normalized["contrast"] == 4
    assert normalized["clarity_enhancement"] == "strong"
    assert normalized["output_stage"] == "original"
    assert has_user_adjustments(normalized) is True


def test_visual_crop_points_create_clamped_bbox() -> None:
    dimensions = {"width": 120, "height": 80}

    assert crop_bbox_from_points([(90, 70), (10, 20)], dimensions) == [10, 20, 90, 70]
    assert crop_bbox_from_points([(-10, 5), (150, 999)], dimensions) == [0, 5, 120, 80]
    assert crop_bbox_from_points([(20, 20), (20, 60)], dimensions) == []


def test_same_size_different_image_resets_crop_state() -> None:
    dimensions = {"width": 120, "height": 80}
    first_identity = image_identity_from_bytes(b"first-image", dimensions)
    second_identity = image_identity_from_bytes(b"second-image", dimensions)
    default_bbox = [0, 0, 120, 80]
    state = {
        "editor_image_identity": first_identity,
        "editor_crop_points": [(90, 70), (10, 20)],
        "editor_x1": 10,
        "editor_y1": 20,
        "editor_x2": 90,
        "editor_y2": 70,
        "editor_last_crop_click_nonce": "old-click",
    }
    params = {
        "crop_editor_key": "editor",
        "crop_x": "90",
        "crop_y": "70",
        "crop_click_nonce": "old-click",
        "unrelated": "keep-me",
    }

    reset = _prepare_crop_state_context(state, params, "editor", second_identity, default_bbox)

    assert reset is True
    assert state["editor_image_identity"] == second_identity
    assert state["editor_crop_points"] == []
    assert [state["editor_x1"], state["editor_y1"], state["editor_x2"], state["editor_y2"]] == default_bbox
    assert "editor_last_crop_click_nonce" not in state
    assert params == {"unrelated": "keep-me"}


def test_same_image_rerun_keeps_crop_state() -> None:
    dimensions = {"width": 120, "height": 80}
    identity = image_identity_from_bytes(b"same-image", dimensions)
    default_bbox = [0, 0, 120, 80]
    state = {
        "editor_image_identity": identity,
        "editor_crop_points": [(10, 20), (90, 70)],
        "editor_x1": 10,
        "editor_y1": 20,
        "editor_x2": 90,
        "editor_y2": 70,
        "editor_last_crop_click_nonce": "click-2",
    }

    reset = _prepare_crop_state_values(state, "editor", identity, default_bbox)

    assert reset is False
    assert state["editor_crop_points"] == [(10, 20), (90, 70)]
    assert [state["editor_x1"], state["editor_y1"], state["editor_x2"], state["editor_y2"]] == [10, 20, 90, 70]
    assert state["editor_last_crop_click_nonce"] == "click-2"


def test_different_size_image_resets_crop_state() -> None:
    first_identity = image_identity_from_bytes(b"image", {"width": 120, "height": 80})
    second_identity = image_identity_from_bytes(b"image", {"width": 240, "height": 160})
    default_bbox = [0, 0, 240, 160]
    state = {
        "editor_image_identity": first_identity,
        "editor_crop_points": [(10, 20), (90, 70)],
        "editor_x1": 10,
        "editor_y1": 20,
        "editor_x2": 90,
        "editor_y2": 70,
        "editor_last_crop_click_nonce": "click-2",
    }

    reset = _prepare_crop_state_values(state, "editor", second_identity, default_bbox)

    assert reset is True
    assert state["editor_crop_points"] == []
    assert [state["editor_x1"], state["editor_y1"], state["editor_x2"], state["editor_y2"]] == default_bbox


def test_invalid_crop_click_is_not_applied_and_query_params_are_cleared() -> None:
    state = {
        "editor_crop_points": [(10, 20)],
        "editor_x1": 10,
        "editor_y1": 20,
        "editor_x2": 90,
        "editor_y2": 70,
    }
    params = {
        "crop_editor_key": "editor",
        "crop_x": "not-a-number",
        "crop_y": "70",
        "crop_click_nonce": "bad-click",
    }

    applied = _consume_crop_click_from_params(state, params, "editor", {"width": 120, "height": 80})

    assert applied is False
    assert state["editor_crop_points"] == [(10, 20)]
    assert [state["editor_x1"], state["editor_y1"], state["editor_x2"], state["editor_y2"]] == [10, 20, 90, 70]
    assert params == {}


def test_reversed_crop_clicks_generate_sorted_bbox_and_clear_query_params() -> None:
    state: dict[str, object] = {"editor_crop_points": []}
    dimensions = {"width": 120, "height": 80}
    first_params = {
        "crop_editor_key": "editor",
        "crop_x": "90",
        "crop_y": "70",
        "crop_click_nonce": "click-1",
    }
    second_params = {
        "crop_editor_key": "editor",
        "crop_x": "10",
        "crop_y": "20",
        "crop_click_nonce": "click-2",
    }

    assert _consume_crop_click_from_params(state, first_params, "editor", dimensions) is True
    assert first_params == {}
    assert _consume_crop_click_from_params(state, second_params, "editor", dimensions) is True

    assert second_params == {}
    assert state["editor_crop_points"] == [(90, 70), (10, 20)]
    assert [state["editor_x1"], state["editor_y1"], state["editor_x2"], state["editor_y2"]] == [10, 20, 90, 70]


def test_apply_user_adjustments_outputs_requested_versions(tmp_path: Path) -> None:
    image = _synthetic_structure()

    cropped = apply_user_adjustments(image, {"crop_bbox": [20, 20, 110, 60], "contrast": 1.3})
    binary = apply_user_adjustments(image, {"output_stage": "binary", "invert": True})
    normalized = apply_user_adjustments(image, {"output_stage": "normalized"}, default_size=(64, 64))
    trimmed = apply_user_adjustments(image, {"trim_whitespace": True})

    assert cropped.shape[0] == 40
    assert cropped.shape[1] == 90
    assert binary.ndim == 2
    assert binary.dtype == np.uint8
    assert set(np.unique(binary)).issubset({0, 255})
    assert normalized.shape == (64, 64)
    assert trimmed.shape[0] < image.shape[0]
    assert trimmed.shape[1] < image.shape[1]

    saved = Path(save_user_adjusted_image(image, {"rotation": 90}, tmp_path / "adjusted.png"))
    assert saved.is_file()
    assert cv2.imread(str(saved)) is not None


def test_clarity_enhancement_sharpens_blurred_strokes_and_preserves_input() -> None:
    original = _synthetic_structure()
    blurred = cv2.GaussianBlur(original, (0, 0), sigmaX=2.0, sigmaY=2.0)
    before = blurred.copy()

    enhanced = apply_user_adjustments(blurred, {"clarity_enhancement": "mild"})

    assert np.array_equal(blurred, before)
    assert enhanced.shape == blurred.shape
    assert enhanced.dtype == np.uint8
    before_sharpness = float(cv2.Laplacian(cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    after_sharpness = float(cv2.Laplacian(cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    assert after_sharpness > before_sharpness
    assert has_user_adjustments({"clarity_enhancement": "mild"}) is True


def test_standard_clarity_enhancement_safely_upscales_low_resolution_image() -> None:
    image = _synthetic_structure()
    enhanced = apply_user_adjustments(image, {"clarity_enhancement": True})

    assert enhanced.shape[:2] == (160, 240)
    assert normalize_user_adjustments({"clarity_enhancement": True})["clarity_enhancement"] == "standard"
    assert normalize_user_adjustments({"clarity_enhancement": "unsupported"})["clarity_enhancement"] == "off"


def test_strong_clarity_enhancement_tightens_white_line_art_edges_and_preserves_colour() -> None:
    image = np.full((80, 160, 3), 255, dtype=np.uint8)
    cv2.line(image, (15, 42), (145, 42), (0, 0, 0), 3)
    cv2.putText(image, "N", (70, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 0, 0), 2)
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=2.2, sigmaY=2.2)

    enhanced = apply_user_adjustments(blurred, {"clarity_enhancement": "strong"})
    baseline = cv2.resize(blurred, (enhanced.shape[1], enhanced.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    baseline_gray = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY)
    enhanced_gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
    baseline_transition = np.mean((baseline_gray > 70) & (baseline_gray < 230))
    enhanced_transition = np.mean((enhanced_gray > 70) & (enhanced_gray < 230))

    assert enhanced.shape[:2] == (160, 320)
    assert enhanced_transition < baseline_transition
    blue_pixels = enhanced[:, :, 0] > (enhanced[:, :, 1] + 40)
    assert np.count_nonzero(blue_pixels) > 0


def test_clarity_enhancement_preserves_numpy_alpha_channel() -> None:
    image = _synthetic_structure()
    alpha = np.full(image.shape[:2], 180, dtype=np.uint8)
    bgra = np.dstack((image, alpha))

    enhanced = apply_user_adjustments(bgra, {"clarity_enhancement": "mild"})

    assert enhanced.shape == bgra.shape
    assert np.array_equal(enhanced[:, :, 3], alpha)


def test_clarity_enhancement_ui_explains_limitations() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "image_editor.py").read_text(encoding="utf-8")
    assert "模糊增强" in source
    assert "标准（推荐）" in source
    assert "强（线稿清晰化）" in source
    assert "可放大查看" in source
    assert "不能恢复原图中已经丢失的原子、键或立体化学信息" in source


def test_preprocess_pipeline_records_uploaded_and_adjusted_stages(tmp_path: Path) -> None:
    image_path = tmp_path / "molecule.png"
    image_path.write_bytes(_encode_png(_synthetic_structure()))

    stages = ImagePreprocessor(default_size=(64, 64)).preprocess_pipeline(
        image_path,
        user_adjustments={"crop_bbox": [10, 10, 110, 70], "output_stage": "grayscale"},
    )

    assert {"uploaded_original", "user_adjusted", "original", "normalized"} <= stages.keys()
    assert stages["uploaded_original"].shape == (80, 120, 3)
    assert stages["user_adjusted"].ndim == 2
    assert stages["normalized"].shape == (64, 64)


def test_image_page_persists_adjusted_input_and_report_metadata(tmp_path: Path) -> None:
    payload = _encode_png(_synthetic_structure())
    image_run = create_image_run_from_bytes(payload, "aspirin.png", runs_root=tmp_path / "runs", analysis_id="analysis-1")
    adjustments = {
        "crop_bbox": [5, 5, 100, 70],
        "rotation": 2.5,
        "invert": False,
        "contrast": 1.1,
        "clarity_enhancement": "standard",
        "trim_whitespace": True,
        "output_stage": "normalized",
    }

    adjusted_input = _prepare_effective_input(image_run, "aspirin.png", payload, adjustments, True)
    report = {"input": {"type": "image"}, "images": {"preprocessing": {}}}
    _attach_user_preprocessing(report, adjustments, adjusted_input, image_run.input_path, True)

    assert adjusted_input.is_file()
    assert adjusted_input.name == "aspirin_user_adjusted.png"
    assert report["user_preprocessing"]["applied"] is True
    assert report["user_preprocessing"]["clarity_enhancement"] == "standard"
    assert report["user_preprocessing"]["effective_image_sha256"]
    assert report["input"]["effective_path"] == str(adjusted_input.resolve())
    assert report["images"]["preprocessing"]["uploaded_original"] == str(image_run.input_path.resolve())
    assert report["images"]["preprocessing"]["user_adjusted"] == str(adjusted_input.resolve())
