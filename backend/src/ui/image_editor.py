"""Lightweight single-image preprocessing editor widgets."""

from __future__ import annotations

import base64
from collections.abc import MutableMapping
import hashlib
import json
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from src.preprocess.user_adjustments import (
    apply_user_adjustments,
    encode_png,
    has_user_adjustments,
    image_dimensions,
    normalize_user_adjustments,
)


OUTPUT_STAGE_LABELS = {
    "original": "原图",
    "grayscale": "灰度",
    "normalized": "归一化",
    "binary": "二值化",
}

CLARITY_LEVEL_LABELS = {
    "off": "关闭",
    "mild": "轻度",
    "standard": "标准（推荐）",
    "strong": "强（线稿清晰化）",
}

CROP_QUERY_KEYS = ("crop_editor_key", "crop_x", "crop_y", "crop_click_nonce")


def render_image_editor(
    image_bytes: bytes,
    filename: str,
    key_prefix: str = "single_image",
    *,
    expanded: bool = False,
    show_json: bool = True,
) -> tuple[dict[str, Any], bytes, bool]:
    """Render lightweight preprocessing controls and return adjustments plus preview bytes."""
    dimensions = image_dimensions(image_bytes)
    default_bbox = [0, 0, dimensions["width"], dimensions["height"]]
    image_identity = image_identity_from_bytes(image_bytes, dimensions)
    _prepare_crop_state(key_prefix, image_identity, default_bbox)
    _consume_crop_click(key_prefix, dimensions)
    container = st.container() if expanded else st.expander("单图预处理编辑器", expanded=False)
    with container:
        st.caption(f"{filename} | {dimensions['width']} × {dimensions['height']}")
        controls = st.columns(4)
        use_crop = controls[0].checkbox("裁剪", value=False, key=f"{key_prefix}_crop_enabled")
        trim_whitespace = controls[1].checkbox("去除白边", value=False, key=f"{key_prefix}_trim")
        invert = controls[2].checkbox("黑白反转", value=False, key=f"{key_prefix}_invert")
        output_stage = controls[3].selectbox(
            "输出版本",
            list(OUTPUT_STAGE_LABELS),
            index=0,
            format_func=lambda value: OUTPUT_STAGE_LABELS[value],
            key=f"{key_prefix}_stage",
        )

        crop_bbox: list[int] = []
        if use_crop:
            preview_bbox = [
                int(st.session_state.get(f"{key_prefix}_x1", default_bbox[0])),
                int(st.session_state.get(f"{key_prefix}_y1", default_bbox[1])),
                int(st.session_state.get(f"{key_prefix}_x2", default_bbox[2])),
                int(st.session_state.get(f"{key_prefix}_y2", default_bbox[3])),
            ]
            _render_crop_picker(image_bytes, filename, key_prefix, dimensions, preview_bbox)
            crop_cols = st.columns(4)
            x1 = crop_cols[0].number_input("x1", min_value=0, max_value=dimensions["width"], key=f"{key_prefix}_x1")
            y1 = crop_cols[1].number_input("y1", min_value=0, max_value=dimensions["height"], key=f"{key_prefix}_y1")
            x2 = crop_cols[2].number_input("x2", min_value=0, max_value=dimensions["width"], key=f"{key_prefix}_x2")
            y2 = crop_cols[3].number_input("y2", min_value=0, max_value=dimensions["height"], key=f"{key_prefix}_y2")
            crop_bbox = [int(x1), int(y1), int(x2), int(y2)]

        rotate_cols = st.columns(4)
        right_angle = rotate_cols[0].selectbox("旋转 90°", [0, 90, 180, 270], index=0, key=f"{key_prefix}_right_angle")
        fine_rotation = rotate_cols[1].slider("小角度旋转", -15.0, 15.0, 0.0, 0.5, key=f"{key_prefix}_fine_rotation")
        contrast = rotate_cols[2].slider("对比度", 0.5, 2.0, 1.0, 0.05, key=f"{key_prefix}_contrast")
        clarity_enhancement = rotate_cols[3].selectbox(
            "模糊增强",
            list(CLARITY_LEVEL_LABELS),
            index=0,
            format_func=lambda value: CLARITY_LEVEL_LABELS[value],
            key=f"{key_prefix}_clarity_enhancement",
            help="保边降噪、局部对比度增强和反锐化；标准/强档会有限放大低分辨率图片。",
        )
        if clarity_enhancement != "off":
            st.warning("模糊增强只能改善现有线条的可读性，不能恢复原图中已经丢失的原子、键或立体化学信息；请核对增强预览。")

        adjustments = normalize_user_adjustments(
            {
                "crop_bbox": crop_bbox,
                "rotation": float(right_angle) + float(fine_rotation),
                "invert": invert,
                "contrast": float(contrast),
                "clarity_enhancement": clarity_enhancement,
                "trim_whitespace": trim_whitespace,
                "output_stage": output_stage,
            }
        )
        try:
            adjusted_image = apply_user_adjustments(image_bytes, adjustments)
            adjusted_bytes = encode_png(adjusted_image)
            preview_width = min(900, max(600, int(adjusted_image.shape[1])))
            st.image(adjusted_bytes, caption="调整后预览（可放大查看）", width=preview_width)
        except Exception as exc:
            st.warning(f"预处理预览失败：{exc}")
            adjusted_bytes = image_bytes
        if show_json:
            st.json({"user_preprocessing": adjustments})
    return adjustments, adjusted_bytes, has_user_adjustments(adjustments)


def crop_bbox_from_points(points: list[tuple[int, int]], dimensions: dict[str, int]) -> list[int]:
    """Return a clamped crop bbox from two clicked image points."""
    if len(points) < 2:
        return []
    width = max(1, int(dimensions.get("width") or 1))
    height = max(1, int(dimensions.get("height") or 1))
    (ax, ay), (bx, by) = points[-2], points[-1]
    x1, x2 = sorted((max(0, min(width, int(ax))), max(0, min(width, int(bx)))))
    y1, y2 = sorted((max(0, min(height, int(ay))), max(0, min(height, int(by)))))
    return [x1, y1, x2, y2] if x2 > x1 and y2 > y1 else []


def image_identity_from_bytes(image_bytes: bytes, dimensions: dict[str, int]) -> dict[str, Any]:
    """Return a stable identity for an editor image, including content hash."""
    return {
        "width": int(dimensions["width"]),
        "height": int(dimensions["height"]),
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def _prepare_crop_state(key_prefix: str, image_identity: dict[str, Any], default_bbox: list[int]) -> None:
    _prepare_crop_state_context(st.session_state, st.query_params, key_prefix, image_identity, default_bbox)


def _prepare_crop_state_context(
    state: MutableMapping[str, Any],
    params: MutableMapping[str, Any],
    key_prefix: str,
    image_identity: dict[str, Any],
    default_bbox: list[int],
) -> bool:
    reset = _prepare_crop_state_values(state, key_prefix, image_identity, default_bbox)
    if reset:
        _clear_crop_query_params(params)
    return reset


def _prepare_crop_state_values(
    state: MutableMapping[str, Any],
    key_prefix: str,
    image_identity: dict[str, Any],
    default_bbox: list[int],
) -> bool:
    identity_key = f"{key_prefix}_image_identity"
    if state.get(identity_key) != image_identity:
        state[identity_key] = dict(image_identity)
        state[f"{key_prefix}_crop_points"] = []
        state.pop(f"{key_prefix}_last_crop_click_nonce", None)
        for key, value in zip(("x1", "y1", "x2", "y2"), default_bbox):
            state[f"{key_prefix}_{key}"] = int(value)
        return True
    for key, value in zip(("x1", "y1", "x2", "y2"), default_bbox):
        state.setdefault(f"{key_prefix}_{key}", int(value))
    state.setdefault(f"{key_prefix}_crop_points", [])
    return False


def _consume_crop_click(key_prefix: str, dimensions: dict[str, int]) -> None:
    _consume_crop_click_from_params(st.session_state, st.query_params, key_prefix, dimensions)


def _consume_crop_click_from_params(
    state: MutableMapping[str, Any],
    params: MutableMapping[str, Any],
    key_prefix: str,
    dimensions: dict[str, int],
) -> bool:
    if params.get("crop_editor_key") != key_prefix:
        return False
    try:
        nonce = str(params.get("crop_click_nonce") or "")
        if not nonce or state.get(f"{key_prefix}_last_crop_click_nonce") == nonce:
            return False
        try:
            x = int(float(str(params.get("crop_x"))))
            y = int(float(str(params.get("crop_y"))))
        except (TypeError, ValueError):
            return False
        width = int(dimensions["width"])
        height = int(dimensions["height"])
        point = (max(0, min(width, x)), max(0, min(height, y)))
        points = list(state.get(f"{key_prefix}_crop_points") or [])
        points = [point] if len(points) >= 2 else [*points, point]
        state[f"{key_prefix}_crop_points"] = points
        state[f"{key_prefix}_last_crop_click_nonce"] = nonce
        bbox = crop_bbox_from_points(points, dimensions)
        if bbox:
            for key, value in zip(("x1", "y1", "x2", "y2"), bbox):
                state[f"{key_prefix}_{key}"] = int(value)
        return True
    finally:
        _clear_crop_query_params(params)


def _clear_crop_query_params(params: MutableMapping[str, Any]) -> None:
    for key in CROP_QUERY_KEYS:
        try:
            if key in params:
                del params[key]
        except KeyError:
            continue


def _render_crop_picker(
    image_bytes: bytes,
    filename: str,
    key_prefix: str,
    dimensions: dict[str, int],
    bbox: list[int],
) -> None:
    points = list(st.session_state.get(f"{key_prefix}_crop_points") or [])
    mime = _image_mime(filename)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    width = int(dimensions["width"])
    height = int(dimensions["height"])
    display_width = min(600, width)
    display_height = max(120, int(display_width * height / max(width, 1)))
    payload = {
        "key": key_prefix,
        "src": f"data:{mime};base64,{encoded}",
        "width": width,
        "height": height,
        "points": points,
        "bbox": bbox,
    }
    html = f"""
    <div style="font: 14px system-ui, sans-serif; color: #0b2f36;">
      <div style="margin-bottom: 6px;">在图上点击两个角点生成裁剪框；再次点击会重新开始。</div>
      <div style="position: relative; display: inline-block; max-width: 100%;">
        <img id="crop-image" src="{payload['src']}" style="width: min(100%, {display_width}px); display: block; cursor: crosshair; border: 1px solid #9ab8b8; border-radius: 6px;" />
        <canvas id="crop-overlay" style="position:absolute; inset:0; pointer-events:none;"></canvas>
      </div>
    </div>
    <script>
      const payload = {json.dumps(payload, ensure_ascii=False)};
      const image = document.getElementById("crop-image");
      const canvas = document.getElementById("crop-overlay");
      const ctx = canvas.getContext("2d");

      function drawOverlay() {{
        const rect = image.getBoundingClientRect();
        canvas.width = Math.max(1, Math.round(rect.width));
        canvas.height = Math.max(1, Math.round(rect.height));
        canvas.style.width = rect.width + "px";
        canvas.style.height = rect.height + "px";
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const sx = canvas.width / payload.width;
        const sy = canvas.height / payload.height;
        const bbox = payload.bbox || [];
        if (bbox.length === 4 && bbox[2] > bbox[0] && bbox[3] > bbox[1]) {{
          ctx.strokeStyle = "#0f766e";
          ctx.lineWidth = 3;
          ctx.fillStyle = "rgba(15, 118, 110, 0.12)";
          const x = bbox[0] * sx;
          const y = bbox[1] * sy;
          const w = (bbox[2] - bbox[0]) * sx;
          const h = (bbox[3] - bbox[1]) * sy;
          ctx.fillRect(x, y, w, h);
          ctx.strokeRect(x, y, w, h);
        }}
        for (const point of payload.points || []) {{
          ctx.beginPath();
          ctx.arc(point[0] * sx, point[1] * sy, 5, 0, Math.PI * 2);
          ctx.fillStyle = "#0f766e";
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = "white";
          ctx.stroke();
        }}
      }}

      image.addEventListener("load", drawOverlay);
      window.addEventListener("resize", drawOverlay);
      image.addEventListener("click", (event) => {{
        const rect = image.getBoundingClientRect();
        const x = Math.round((event.clientX - rect.left) * payload.width / rect.width);
        const y = Math.round((event.clientY - rect.top) * payload.height / rect.height);
        const params = new URLSearchParams(window.top.location.search);
        params.set("crop_editor_key", payload.key);
        params.set("crop_x", String(Math.max(0, Math.min(payload.width, x))));
        params.set("crop_y", String(Math.max(0, Math.min(payload.height, y))));
        params.set("crop_click_nonce", String(Date.now()));
        window.top.location.href = window.top.location.pathname + "?" + params.toString();
      }});
      drawOverlay();
    </script>
    """
    components.html(html, height=display_height + 48)


def _image_mime(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
