"""Consistent image preview helpers for Streamlit pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image

from config import OUTPUT_DIR

UPLOAD_PREVIEW_WIDTH = 600
STRUCTURE_PREVIEW_WIDTH = 480
PREPROCESS_PREVIEW_WIDTH = 260
DOCUMENT_PREVIEW_WIDTH = 900
DOCUMENT_PREVIEW_MAX_HEIGHT = 1400


def show_upload_preview(image: Any, caption: str | None = None) -> None:
    st.image(image, caption=caption, width=UPLOAD_PREVIEW_WIDTH)


def show_structure(image_path: str | Path | None, caption: str = "分子结构图") -> None:
    if image_path and Path(image_path).is_file():
        st.image(str(image_path), caption=caption, width=STRUCTURE_PREVIEW_WIDTH)


def show_preprocess_thumbnail(image_path: str | Path, caption: str) -> None:
    st.image(str(image_path), caption=caption, width=PREPROCESS_PREVIEW_WIDTH)


def show_document_page(image_path: str | Path, caption: str) -> None:
    """Show a bounded document thumbnail and avoid eager rendering of large source pages."""
    path = Path(image_path)
    if not path.is_file():
        st.warning(f"预览图片不存在：{path}")
        return
    preview_path = _document_preview_path(path)
    try:
        if not preview_path.is_file() or preview_path.stat().st_mtime < path.stat().st_mtime:
            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((DOCUMENT_PREVIEW_WIDTH, DOCUMENT_PREVIEW_MAX_HEIGHT))
                preview.save(preview_path, format="PNG")
    except Exception as exc:
        st.warning(f"预览图片无法读取：{exc}")
        return
    st.image(str(preview_path), caption=caption, width=DOCUMENT_PREVIEW_WIDTH)
    with st.expander("查看大图"):
        st.caption(f"大图文件：{path}")
        st.caption("为避免大页面图像导致 Streamlit 断联，这里不自动渲染原始大图；可从结果包中查看原始标注页。")


def _document_preview_path(path: Path) -> Path:
    preview_dir = OUTPUT_DIR / "ui_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    return preview_dir / f"{path.stem}_{path.stat().st_mtime_ns}_preview.png"
