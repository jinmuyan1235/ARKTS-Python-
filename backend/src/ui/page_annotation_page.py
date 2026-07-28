"""Streamlit page-level bbox annotation workspace."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw
import streamlit as st

from src.datasets.page_annotations import PAGE_REGION_CLASSES, PageAnnotationStore
from src.documents.detectors import HeuristicMoleculeRegionDetector
from src.documents.models import DocumentPage
from src.ui.drawable_canvas_compat import st_canvas_compat


LAYOUT_TAGS = ("structure_dense", "reaction_scheme", "table_or_sar", "ordinary_text")
CANVAS_MAX_WIDTH = 1080
CLASS_COLORS = {
    "molecule": "#16a34a",
    "reaction": "#dc2626",
    "multiple_molecules": "#9333ea",
    "text": "#2563eb",
    "table": "#ca8a04",
    "figure": "#ea580c",
    "logo": "#0891b2",
    "ignore": "#64748b",
}
CLASS_LABELS = {
    "molecule": "单个分子",
    "reaction": "反应区域/箭头",
    "multiple_molecules": "无法拆开的多个分子",
    "text": "文字",
    "table": "表格",
    "figure": "普通图片",
    "logo": "Logo",
    "ignore": "忽略",
}


def _proposal_boxes(page: dict, root: Path, profile: str) -> list[tuple[int, int, int, int]]:
    image_path = root / page["image_path"]
    model_page = DocumentPage(
        document_id=page["source_document"], page_number=int(page["page_number"]),
        image_path=str(image_path), width=int(page["width"]), height=int(page["height"]),
    )
    detector = HeuristicMoleculeRegionDetector(proposal_config=profile, crop_screening_config="candidate")
    return [tuple(region.bbox) for region in detector.propose(model_page)]


def _overlay(page: dict, root: Path, baseline: bool, candidate: bool) -> Image.Image:
    image = Image.open(root / page["image_path"]).convert("RGB")
    draw = ImageDraw.Draw(image)
    if baseline:
        for bbox in _proposal_boxes(page, root, "baseline"):
            draw.rectangle(bbox, outline="#e74c3c", width=3)
    if candidate:
        for bbox in _proposal_boxes(page, root, "candidate"):
            draw.rectangle(bbox, outline="#3465d9", width=3)
    return image


def _canvas_object(annotation: dict, scale: float) -> dict:
    x1, y1, x2, y2 = annotation["bbox"]
    color = CLASS_COLORS[annotation["class"]]
    return {
        "type": "rect", "version": "4.4.0", "originX": "left", "originY": "top",
        "left": x1 * scale, "top": y1 * scale,
        "width": (x2 - x1) * scale, "height": (y2 - y1) * scale,
        "fill": color + "18", "stroke": color, "strokeWidth": 3,
        "strokeUniform": True, "scaleX": 1, "scaleY": 1, "angle": 0,
        "flipX": False, "flipY": False, "opacity": 1, "visible": True,
        "lockRotation": True, "hasRotatingPoint": False,
    }


def _annotation_from_canvas_object(item: dict, scale: float, fallback_class: str) -> dict | None:
    if item.get("type") != "rect":
        return None
    width = float(item.get("width") or 0) * float(item.get("scaleX") or 1)
    height = float(item.get("height") or 0) * float(item.get("scaleY") or 1)
    if width < 3 or height < 3:
        return None
    left, top = float(item.get("left") or 0), float(item.get("top") or 0)
    color = str(item.get("stroke") or "").lower()
    color_to_class = {value.lower(): key for key, value in CLASS_COLORS.items()}
    region_class = color_to_class.get(color, fallback_class)
    return {
        "bbox": [
            round(left / scale), round(top / scale),
            round((left + width) / scale), round((top + height) / scale),
        ],
        "class": region_class,
    }


def render_page_annotation_workspace() -> None:
    root = Path(os.getenv("OCSR_PAGE_ANNOTATION_ROOT", "data/page_annotations/visual-page-holdout-v0.1"))
    try:
        store = PageAnnotationStore(root)
    except FileNotFoundError as exc:
        st.info(str(exc)); return
    payload = store.load()
    page_ids = store.page_ids()
    completed = sum(page.get("annotation_status") == "completed" for page in payload["pages"].values())
    st.subheader("页面级框标注")
    st.caption(
        f"已保存 {completed}/{len(page_ids)} 页。人工真值标注时请保持机器框关闭，不要参考 OCSR 输出。"
    )
    with st.expander("操作说明（第一次请先看）", expanded=completed == 0):
        st.markdown(
            "1. 选择 **绘制新框** 和类别，然后直接在图片上按住鼠标拖出矩形。  \n"
            "2. 选择 **调整/删除框**，点击已有框后可拖动、缩放；按 Delete 或 Backspace 删除。画错可点画布工具栏的撤销。  \n"
            "3. 每个可分开的分子画一个紧框，尽量排除编号、箭头、条件和图注。  \n"
            "4. 本页完成后，点击画布下方工具栏最左侧的 **↓ 同步按钮**；看到“已同步”后再保存。  \n"
            "5. 填写标注人并点击 **保存已同步方框并进入下一页**。没有单分子的页面可直接点 **确认空页并进入下一页**。"
        )
    if pending_page := st.session_state.pop("page_annotation_next", None):
        st.session_state["page_annotation_selected"] = pending_page
    selected = st.selectbox("论文 / 页码", page_ids, key="page_annotation_selected")
    page = store.page(selected)
    toggles = st.columns(3)
    show_baseline = toggles[0].toggle("Show baseline boxes", value=False, key="page_show_baseline")
    show_candidate = toggles[1].toggle("Show candidate boxes", value=False, key="page_show_candidate")
    toggles[2].caption("机器框仅用于保存后检查：红=baseline，蓝=candidate")
    draft_key = f"page_annotation_draft_{selected}"
    sync_key = f"page_annotation_synced_{selected}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = [
            {"bbox": list(item["bbox"]), "class": item["class"]}
            for item in page.get("annotations", [])
        ]
    draft = st.session_state[draft_key]
    controls = st.columns([0.42, 0.42, 0.16])
    interaction = controls[0].segmented_control(
        "鼠标操作", ["绘制新框", "调整/删除框"], default="绘制新框",
        key=f"page_canvas_mode_{selected}",
    )
    active_class = controls[1].selectbox(
        "新框类别", PAGE_REGION_CLASSES,
        format_func=lambda value: f"{CLASS_LABELS[value]} ({value})",
        key=f"page_canvas_class_{selected}",
    )
    if controls[2].button("清空本页", key=f"page_clear_{selected}"):
        st.session_state[draft_key] = []
        st.session_state[sync_key] = False
        st.session_state[f"page_canvas_revision_{selected}"] = st.session_state.get(
            f"page_canvas_revision_{selected}", 0,
        ) + 1
        st.rerun()
    source = _overlay(page, store.root, show_baseline, show_candidate)
    original_width, original_height = source.size
    scale = min(1.0, CANVAS_MAX_WIDTH / max(original_width, 1))
    canvas_width = max(1, round(original_width * scale))
    canvas_height = max(1, round(original_height * scale))
    background = source.resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)
    initial_drawing = {
        "version": "4.4.0",
        "objects": [_canvas_object(item, scale) for item in draft],
    }
    color = CLASS_COLORS[active_class]
    st.info("流畅标注模式：画框时页面不会刷新。本页完成后，只需点击画布下方最左侧的 ↓ 按钮同步一次。")
    canvas_result = st_canvas_compat(
        fill_color=color + "18", stroke_width=3, stroke_color=color,
        background_image=background, update_streamlit=False,
        height=canvas_height, width=canvas_width,
        drawing_mode="rect" if interaction == "绘制新框" else "transform",
        initial_drawing=initial_drawing, display_toolbar=True,
        key=(
            f"page_canvas_{selected}_"
            f"{st.session_state.get(f'page_canvas_revision_{selected}', 0)}"
        ),
    )
    edited_rows = draft
    if canvas_result.json_data is not None:
        canvas_rows = []
        for item in canvas_result.json_data.get("objects", []):
            converted = _annotation_from_canvas_object(item, scale, active_class)
            if converted is not None:
                canvas_rows.append(converted)
        if canvas_rows != draft:
            edited_rows = canvas_rows
            st.session_state[draft_key] = canvas_rows
            st.session_state[sync_key] = True
    canvas_is_synced = bool(st.session_state.get(sync_key, False))
    if canvas_is_synced:
        st.success(f"本页方框已同步：{len(edited_rows)} 个。若继续修改，请再次点击 ↓ 同步。")
    counts: dict[str, int] = {}
    for item in edited_rows:
        counts[item["class"]] = counts.get(item["class"], 0) + 1
    st.caption(
        f"当前共 {len(edited_rows)} 个框"
        + ("：" + "，".join(f"{CLASS_LABELS[key]} {value}" for key, value in counts.items()) if counts else "")
    )
    annotator = st.text_input("标注人", value=page.get("annotator", ""), key=f"page_annotator_{selected}")
    saved_layout_tags = page.get("layout_tags", [])
    if not isinstance(saved_layout_tags, list):
        saved_layout_tags = []
    layout_tags = st.multiselect(
        "页面类型（只描述页面，不参与抽样）",
        LAYOUT_TAGS, default=saved_layout_tags, key=f"page_layout_{selected}",
    )
    if not canvas_is_synced:
        st.caption("有单分子时，保存按钮将在点击画布下方最左侧的 ↓ 同步按钮后启用。")
    save_columns = st.columns([0.62, 0.38])
    save_clicked = save_columns[0].button(
        "保存已同步方框并进入下一页", type="primary", key=f"page_save_{selected}",
        disabled=not canvas_is_synced,
    )
    empty_clicked = save_columns[1].button(
        "确认空页并进入下一页", key=f"page_save_empty_{selected}",
        help="仅当本页没有任何可见单分子结构时使用。",
    )
    if save_clicked or empty_clicked:
        rows_to_save = [] if empty_clicked else edited_rows
        try:
            store.save_page(selected, rows_to_save, annotator=annotator, layout_tags=layout_tags)
        except (KeyError, TypeError, ValueError) as exc:
            st.error(str(exc))
        else:
            current_index = page_ids.index(selected)
            if current_index + 1 < len(page_ids):
                st.session_state["page_annotation_next"] = page_ids[current_index + 1]
            st.session_state["solo_review_notice"] = f"已保存 {selected}：{len(rows_to_save)} 个框。"
            st.rerun()
