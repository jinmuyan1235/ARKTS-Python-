"""Compatibility wrapper for streamlit-drawable-canvas on recent Streamlit."""

from __future__ import annotations

from copy import deepcopy
from hashlib import md5

import numpy as np
from PIL import Image
import streamlit as st
from streamlit.elements.lib.image_utils import image_to_url
from streamlit.elements.lib.layout_utils import LayoutConfig
from streamlit_drawable_canvas import CanvasResult, _component_func, _data_url_to_image


def background_image_url(image: Image.Image, width: int, key: str | None) -> str:
    """Register a canvas background with Streamlit's current media API."""

    url = image_to_url(
        image,
        LayoutConfig(width=width),
        True,
        "RGB",
        "PNG",
        f"drawable-canvas-bg-{md5(image.tobytes()).hexdigest()}-{key}",
    )
    base_path = st._config.get_option("server.baseUrlPath")
    return f"{base_path}{url}"


def st_canvas_compat(
    *,
    fill_color: str = "#eee",
    stroke_width: int = 20,
    stroke_color: str = "black",
    background_color: str = "",
    background_image: Image.Image | None = None,
    update_streamlit: bool = True,
    height: int = 400,
    width: int = 600,
    drawing_mode: str = "freedraw",
    initial_drawing: dict | None = None,
    display_toolbar: bool = True,
    point_display_radius: int = 3,
    key: str | None = None,
) -> CanvasResult:
    """Render the drawable canvas with a Streamlit-1.59-safe background."""

    image_url = background_image_url(background_image, width, key) if background_image else None
    if image_url:
        background_color = ""
    drawing = deepcopy(initial_drawing) if initial_drawing is not None else {"version": "4.4.0"}
    drawing["background"] = background_color
    component_value = _component_func(
        fillColor=fill_color,
        strokeWidth=stroke_width,
        strokeColor=stroke_color,
        backgroundColor=background_color,
        backgroundImageURL=image_url,
        realtimeUpdateStreamlit=update_streamlit and drawing_mode != "polygon",
        canvasHeight=height,
        canvasWidth=width,
        drawingMode=drawing_mode,
        initialDrawing=drawing,
        displayToolbar=display_toolbar,
        displayRadius=point_display_radius,
        key=key,
        default=None,
    )
    if component_value is None:
        return CanvasResult()
    return CanvasResult(
        np.asarray(_data_url_to_image(component_value["data"])),
        component_value["raw"],
    )
