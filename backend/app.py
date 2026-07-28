"""Streamlit entry point for the molecule recognition application."""

from __future__ import annotations

import streamlit as st

import config
from src.runtime.health import image_workflows_enabled, run_production_health_check
from src.runtime.run_store import cleanup_runs_if_due
from src.ui.about_page import render_about_page
from src.ui.batch_page import render_batch_page
from src.ui.dataset_review_page import render_dataset_review_page
from src.ui.document_page import render_document_page
from src.ui.health_page import render_blocked_workflow, render_health_banner, render_health_page
from src.ui.history_page import render_history_page
from src.ui.image_page import render_image_page
from src.ui.report_view import show_report
from src.ui.review_queue_page import render_review_queue_page
from src.ui.sidebar import render_sidebar
from src.ui.smiles_page import render_smiles_page
from src.ui.state import current_runtime_key, runtime_config_from_key
from src.ui.styles import apply_styles, reset_main_scroll


PAGE_LABELS = {
    "dataset_review": "Data Management / OCSR Dataset Review",
    "image": "工作台 / 图片识别",
    "document": "工作台 / 文档识别",
    "batch": "工作台 / 批量处理",
    "smiles": "工作台 / SMILES 分析",
    "history": "数据管理 / 分析历史",
    "review": "数据管理 / 审核队列",
    "health": "系统 / 健康检查",
    "about": "系统 / 项目说明",
}


def _render_page_picker() -> str:
    with st.sidebar:
        st.divider()
        return st.radio(
            "页面",
            list(PAGE_LABELS),
            format_func=PAGE_LABELS.get,
            key="active_page",
        )


config.initialize_directories()
st.set_page_config(page_title="分子结构识别与性质分析", page_icon="🧪", layout="wide")
apply_styles()

st.title("分子结构识别与性质分析")
st.caption(f"图片/PDF → OpenCV 区域处理 → OCSR → SMILES → RDKit 校验与报告；当前模式：{config.APP_MODE}")
st.session_state["run_cleanup"] = cleanup_runs_if_due()

selected_backend, show_preprocessing, export_pdf = render_sidebar()
active_page = _render_page_picker()
runtime_key = current_runtime_key()
reset_main_scroll(f"{active_page}:{selected_backend}:{runtime_key}")
health_force_refresh = bool(st.session_state.pop("health_force_refresh", False))
health_full_warmup = bool(st.session_state.pop("health_full_warmup", False))
production_health = run_production_health_check(
    selected_backend,
    runtime_config=runtime_config_from_key(runtime_key),
    production=config.IS_PRODUCTION_MODE,
    # A sidebar selection reruns the app. Do not block that interaction by
    # loading and executing large OCSR models; the health page requests it.
    warmup=health_full_warmup and config.IS_PRODUCTION_MODE,
    load_model=health_full_warmup and config.IS_PRODUCTION_MODE,
    force=health_force_refresh,
    use_cache=config.PRODUCTION_HEALTH_CACHE_ENABLED,
)
st.session_state["production_health"] = production_health
if config.IS_PRODUCTION_MODE:
    render_health_banner(production_health)
real_ocsr_enabled = image_workflows_enabled(production_health)


def _render_image_page() -> None:
    if real_ocsr_enabled:
        render_image_page(selected_backend, show_preprocessing, export_pdf)
    else:
        render_blocked_workflow(production_health, "图片识别")


def _render_document_nav_page() -> None:
    if real_ocsr_enabled:
        render_document_page(selected_backend)
    else:
        render_blocked_workflow(production_health, "文档识别")


def _render_smiles_nav_page() -> None:
    render_smiles_page(export_pdf)


def _render_batch_nav_page() -> None:
    if real_ocsr_enabled:
        render_batch_page(selected_backend)
    else:
        render_blocked_workflow(production_health, "批量识别")


def _render_history_nav_page() -> None:
    render_history_page(selected_backend, show_preprocessing, export_pdf)


def _render_review_nav_page() -> None:
    render_review_queue_page()


def _render_dataset_review_nav_page() -> None:
    render_dataset_review_page()


def _render_health_nav_page() -> None:
    render_health_page(production_health)


def _render_about_nav_page() -> None:
    render_about_page()


if active_page == "image":
    _render_image_page()
elif active_page == "document":
    _render_document_nav_page()
elif active_page == "batch":
    _render_batch_nav_page()
elif active_page == "smiles":
    _render_smiles_nav_page()
elif active_page == "history":
    _render_history_nav_page()
elif active_page == "review":
    _render_review_nav_page()
elif active_page == "dataset_review":
    _render_dataset_review_nav_page()
elif active_page == "health":
    _render_health_nav_page()
elif active_page == "about":
    _render_about_nav_page()
