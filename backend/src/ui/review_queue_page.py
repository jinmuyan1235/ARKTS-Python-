"""Independent feedback review queue page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from config import DATA_DIR, OUTPUT_DIR
from src.analysis.correction import structure_similarity
from src.chem.mol_drawer import draw_molecule
from src.feedback.review_service import FeedbackReviewService
from src.storage.analysis_repository import AnalysisRepository
from src.ui.image_viewer import show_structure
from src.ui.records import render_records
from src.ui.styles import page_intro
from src.utils.file_utils import safe_stem


STATUS_OPTIONS = {
    "待审核": "pending",
    "已通过": "verified",
    "退回修改": "returned",
    "已拒绝": "rejected",
    "重复样本": "duplicate",
    "许可不明": "license_unclear",
    "全部": "all",
}


def render_review_queue_page() -> None:
    """Render the human review queue for correction feedback."""
    page_intro("审核队列", "纠错样本先进入待审核队列，只有审核通过后才会进入训练集导出。")
    service = FeedbackReviewService(DATA_DIR)
    controls = st.columns([0.34, 0.18, 0.12, 0.18, 0.18])
    query = controls[0].text_input("搜索", value="", placeholder="analysis_id / SMILES / 来源 / 图片哈希")
    status_label = controls[1].selectbox("状态", list(STATUS_OPTIONS), index=0)
    limit = controls[2].number_input("数量", min_value=10, max_value=300, value=50, step=10)
    reviewer = controls[3].text_input("审核人", value=st.session_state.get("reviewer_name", ""), key="reviewer_name")
    export_path = OUTPUT_DIR / "feedback_review_manifest.csv"
    if controls[4].button("导出已通过清单", key="export_verified_feedback"):
        result = service.export_verified_manifest(export_path)
        st.success(f"已导出 {result['exported_count']} 条：{result['output_manifest']}")

    items = service.list_items(STATUS_OPTIONS[status_label], query=query, limit=int(limit))
    st.caption(f"匹配样本：{len(items)}")
    if not items:
        st.info("暂无待处理样本。单图纠错页保存的样本会先出现在这里。")
        return
    for item in items:
        _render_review_item(service, item, reviewer)


def _render_review_item(service: FeedbackReviewService, item: dict[str, Any], reviewer: str = "") -> None:
    analysis_id = str(item.get("analysis_id") or "analysis")
    reviewer_name = reviewer.strip()
    reviewer_missing = not reviewer_name
    title = f"{analysis_id} | {item.get('review_status') or '-'} | {item.get('correction_type') or '-'}"
    with st.expander(title, expanded=False):
        image_columns = st.columns(3)
        with image_columns[0]:
            st.caption("原图")
            original = item.get("image_path_abs")
            if original:
                st.image(original, use_column_width=True)
            else:
                st.info("原图不可用")
        with image_columns[1]:
            st.caption("模型预测重绘")
            predicted_image = _structure_image(service, item, "predicted")
            if predicted_image:
                show_structure(predicted_image, "模型预测重绘")
            else:
                st.info("预测结构不可用")
        with image_columns[2]:
            st.caption("人工修正重绘")
            corrected_image = _structure_image(service, item, "corrected")
            if corrected_image:
                show_structure(corrected_image, "人工修正重绘")
            else:
                st.info("修正结构不可用")

        predicted = item.get("predicted_smiles") or ""
        corrected = item.get("corrected_smiles") or ""
        similarity = structure_similarity(predicted, corrected)
        details = {
            "预测 SMILES": predicted,
            "修正 SMILES": corrected,
            "相似度": "-" if similarity is None else f"{similarity:.3f}",
            "纠错类型": item.get("correction_type"),
            "来源": item.get("source_reference"),
            "许可": item.get("source_license"),
            "模型": item.get("model_name"),
            "版本": item.get("model_version"),
            "审核人": item.get("reviewer"),
            "审核时间": item.get("reviewed_at"),
            "修订版本": item.get("revision"),
            "最近修改人": item.get("revised_by"),
            "最近修改时间": item.get("revised_at"),
            "备注": (item.get("feedback") or {}).get("notes"),
        }
        st.json(details)
        history = item.get("history") or []
        if history:
            render_records(history, title_keys=("source", "operation"), summary_keys=("previous_smiles", "new_smiles", "created_at"))

        _render_original_report_preview(analysis_id)
        _render_revision_form(service, item, reviewer_name)

        reviewer_notes = st.text_area("审核备注", value="", key=f"review_notes_{analysis_id}", height=70)
        duplicate_of = st.text_input("重复来源 analysis_id（标记重复时可填）", value=item.get("duplicate_of") or "", key=f"duplicate_of_{analysis_id}")
        if reviewer_missing:
            st.info("请先填写审核人，才能执行通过、退回、拒绝、重复或许可不明操作。")
        actions = st.columns(5)
        if actions[0].button("通过并进入数据集", key=f"approve_{analysis_id}", disabled=reviewer_missing):
            service.approve_for_dataset(analysis_id, reviewer_notes, reviewer=reviewer_name)
            st.success("已通过审核，样本将进入训练集导出。")
            st.rerun()
        if actions[1].button("退回修改", key=f"return_{analysis_id}", disabled=reviewer_missing):
            service.return_for_revision(analysis_id, reviewer_notes, reviewer=reviewer_name)
            st.warning("已退回修改。")
            st.rerun()
        if actions[2].button("拒绝样本", key=f"reject_{analysis_id}", disabled=reviewer_missing):
            service.reject_sample(analysis_id, reviewer_notes, reviewer=reviewer_name)
            st.warning("已拒绝样本。")
            st.rerun()
        if actions[3].button("标记重复", key=f"duplicate_{analysis_id}", disabled=reviewer_missing):
            service.mark_duplicate(analysis_id, duplicate_of=duplicate_of, reviewer_notes=reviewer_notes, reviewer=reviewer_name)
            st.warning("已标记为重复样本。")
            st.rerun()
        if actions[4].button("标记许可不明", key=f"license_{analysis_id}", disabled=reviewer_missing):
            service.mark_license_unclear(analysis_id, reviewer_notes, reviewer=reviewer_name)
            st.warning("已标记为许可不明。")
            st.rerun()


def _render_original_report_preview(analysis_id: str) -> None:
    if st.button("打开原报告", key=f"open_review_original_{analysis_id}"):
        st.session_state[f"review_original_open_{analysis_id}"] = not st.session_state.get(f"review_original_open_{analysis_id}", False)
    if not st.session_state.get(f"review_original_open_{analysis_id}", False):
        return
    report = AnalysisRepository().load_report(analysis_id)
    st.subheader("原报告预览")
    if report:
        final = report.get("final") or {}
        ocsr = report.get("ocsr") or {}
        validation = report.get("validation") or {}
        st.json({
            "analysis_id": report.get("analysis_id"),
            "status": report.get("status"),
            "message": report.get("message"),
            "backend": ocsr.get("backend"),
            "predicted_smiles": ocsr.get("predicted_smiles") or ocsr.get("smiles"),
            "final_smiles": final.get("smiles"),
            "canonical_smiles": final.get("canonical_smiles") or validation.get("canonical_smiles"),
            "report_path": (report.get("run") or {}).get("report_path"),
        })
        show_structure((report.get("images") or {}).get("redrawn_molecule"), "原报告最终结构")
    else:
        st.info("历史库中没有找到完整原报告；可继续根据审核标注中的原图、预测结构和修正结构判断。")


def _render_revision_form(service: FeedbackReviewService, item: dict[str, Any], reviewer: str = "") -> None:
    if item.get("review_status") != "returned":
        return
    analysis_id = str(item.get("analysis_id") or "analysis")
    revised_by = reviewer.strip()
    reviewer_missing = not revised_by
    st.subheader("退回修改")
    revised_smiles = st.text_input(
        "修订 SMILES",
        value=item.get("corrected_smiles") or "",
        key=f"review_revision_smiles_{analysis_id}",
    )
    revision_notes = st.text_area("修订说明", value="", key=f"review_revision_notes_{analysis_id}", height=70)
    if reviewer_missing:
        st.info("请先填写审核人，重新提交时会记录为 revised_by。")
    if st.button("重新提交审核", key=f"resubmit_review_{analysis_id}", type="primary", disabled=reviewer_missing):
        try:
            result = service.revise_and_resubmit(
                analysis_id,
                revised_smiles,
                revised_by=revised_by,
                notes=revision_notes,
            )
            st.success(f"已保存第 {result['revision']} 版修订，并重新提交审核。")
            st.rerun()
        except Exception as exc:
            st.error(f"重新提交失败：{exc}")


def _structure_image(service: FeedbackReviewService, item: dict[str, Any], kind: str) -> str | None:
    annotation = item.get("annotation") or {}
    images = annotation.get("images") or {}
    if kind == "predicted":
        existing = images.get("predicted_molecule")
        smiles = item.get("predicted_smiles")
    else:
        existing = images.get("corrected_molecule") or images.get("redrawn_molecule")
        smiles = item.get("corrected_smiles")
    if existing and Path(str(existing)).is_file():
        return str(Path(str(existing)).resolve())
    if not smiles:
        return None
    output = service.root / "review_structures" / f"{safe_stem(str(item.get('analysis_id') or 'analysis'))}_{kind}.png"
    try:
        if output.is_file():
            return str(output.resolve())
        return draw_molecule(str(smiles), output)
    except Exception:
        return None
