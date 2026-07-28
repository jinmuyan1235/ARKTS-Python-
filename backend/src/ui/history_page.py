"""Searchable local analysis history page."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

import streamlit as st

from config import OUTPUT_DIR
from src.analysis.molecule_report import MoleculeReportGenerator
from src.export.json_exporter import to_json_text
from src.export.structure_exporter import export_structure_files
from src.runtime.run_store import create_image_run_from_file, save_run_report
from src.storage.analysis_repository import (
    ARTIFACT_STATUS_AVAILABLE,
    ARTIFACT_STATUS_EXPIRED,
    ARTIFACT_STATUS_MISSING,
    AnalysisRepository,
    record_report,
)
from src.ui.report_view import show_report
from src.ui.state import current_runtime_key, runtime_config_from_key
from src.ui.styles import page_intro


STATUS_OPTIONS = {
    "全部": "all",
    "成功": "success",
    "待审核": "review_needed",
    "拒绝": "rejected",
    "失败": "failed",
}


def render_history_page(backend: str, show_preprocessing: bool, export_pdf: bool) -> None:
    """Render local analysis history search and actions."""
    page_intro("分析历史", "按文件名、SMILES、InChIKey 或分析 ID 搜索本地历史记录。")
    delete_notice = st.session_state.pop("history_delete_notice", None)
    delete_warning = st.session_state.pop("history_delete_warning", None)
    if delete_notice:
        st.success(delete_notice)
    if delete_warning:
        st.warning(delete_warning)
    repository = AnalysisRepository()
    controls = st.columns([0.46, 0.24, 0.15, 0.15])
    query = controls[0].text_input("搜索", value="", placeholder="文件名 / SMILES / InChIKey / analysis_id")
    status_label = controls[1].selectbox("状态", list(STATUS_OPTIONS), index=0)
    favorites_only = controls[2].checkbox("只看收藏", value=False)
    limit = controls[3].number_input("数量", min_value=10, max_value=500, value=100, step=10)
    rows = repository.list_analyses(
        query=query,
        status_filter=STATUS_OPTIONS[status_label],
        favorites_only=favorites_only,
        limit=int(limit),
    )
    st.caption(f"匹配记录：{len(rows)}")
    _render_batch_exports(repository, rows)
    if not rows:
        st.info("暂无匹配记录。新的图片、SMILES、文档区域和批量结果会自动进入这里。")
        return

    for row in rows:
        _render_history_row(repository, row, backend)

    report = st.session_state.get("history_report")
    if report:
        st.divider()
        st.subheader("历史报告预览")
        show_report(report, show_preprocessing, export_pdf, f"history_{str(report.get('analysis_id', 'report'))[:8]}")


def _render_batch_exports(repository: AnalysisRepository, rows: list[dict]) -> None:
    left, right = st.columns(2)
    left.download_button(
        "批量导出历史 CSV",
        repository.export_rows_csv(rows).encode("utf-8-sig"),
        "analysis_history.csv",
        "text/csv",
        key="history_export_csv",
        disabled=not rows,
    )
    zip_bytes = _reports_zip(repository, rows)
    right.download_button(
        "批量导出报告 ZIP",
        zip_bytes,
        "analysis_reports.zip",
        "application/zip",
        key="history_export_reports_zip",
        disabled=not zip_bytes,
    )


def _render_history_row(repository: AnalysisRepository, row: dict, backend: str) -> None:
    analysis_id = str(row["analysis_id"])
    artifact_status = str(row.get("artifact_status") or ARTIFACT_STATUS_MISSING)
    artifact_label = _artifact_status_label(artifact_status)
    report_available = artifact_status == ARTIFACT_STATUS_AVAILABLE
    input_available = Path(str(row.get("input_path") or "")).is_file()
    title = (
        f"{'★ ' if row.get('is_favorite') else ''}{row.get('filename') or analysis_id} | "
        f"{row.get('status') or '-'} | {row.get('decision') or '-'} | {artifact_label}"
    )
    with st.expander(title, expanded=False):
        fields = {
            "analysis_id": analysis_id,
            "created_at": row.get("created_at"),
            "backend": row.get("backend"),
            "final_smiles": row.get("final_smiles"),
            "inchikey": row.get("inchikey"),
            "report_path": row.get("report_path"),
            "delete_status": row.get("delete_status"),
            "delete_updated_at": row.get("delete_updated_at"),
            "artifact_status": row.get("artifact_status"),
            "artifact_reason": row.get("artifact_reason"),
        }
        st.json(fields)
        _render_artifact_status(row, input_available)
        _render_delete_recovery(repository, row, analysis_id)
        actions = st.columns(7)
        if actions[0].button("打开旧报告", key=f"history_open_{analysis_id}", disabled=not report_available):
            report = repository.load_report(analysis_id)
            if report:
                st.session_state["history_report"] = report
                st.rerun()
            else:
                st.warning("报告文件不存在或无法定位该 analysis_id。")
        if actions[1].button("重新识别", key=f"history_rerun_{analysis_id}", disabled=not input_available):
            _rerun_analysis(repository, row, backend)
        if actions[2].button("重新导出", key=f"history_reexport_{analysis_id}", disabled=not report_available):
            _reexport_analysis(repository, analysis_id)
        favorite_label = "取消收藏" if row.get("is_favorite") else "标记收藏"
        if actions[3].button(favorite_label, key=f"history_fav_{analysis_id}"):
            repository.set_favorite(analysis_id, not bool(row.get("is_favorite")))
            st.rerun()
        if actions[4].button("从历史中移除", key=f"history_remove_{analysis_id}"):
            repository.delete_analysis(analysis_id)
            _clear_open_history_report(analysis_id)
            st.session_state["history_delete_notice"] = "已从历史中移除；报告文件和运行目录已保留。"
            st.rerun()
        confirm_key = f"history_delete_files_confirm_{analysis_id}"
        if actions[5].button("删除记录及本地文件", key=f"history_delete_files_{analysis_id}"):
            st.session_state[confirm_key] = True
        if actions[6].button("复制 ID", key=f"history_copy_{analysis_id}"):
            st.code(analysis_id, language=None)
        if st.session_state.get(confirm_key):
            st.warning("确认后会从历史中移除，并删除该分析拥有的运行目录或单报告文件；共享批量结果和外部原图不会删除。")
            confirm_actions = st.columns([0.24, 0.16, 0.60])
            if confirm_actions[0].button("确认删除本地文件", key=f"history_delete_files_yes_{analysis_id}", type="primary"):
                _delete_analysis_and_files_with_notice(repository, analysis_id)
                st.session_state.pop(confirm_key, None)
                st.rerun()
            if confirm_actions[1].button("取消", key=f"history_delete_files_no_{analysis_id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()


def _artifact_status_label(status: str) -> str:
    labels = {
        ARTIFACT_STATUS_AVAILABLE: "报告文件可用",
        ARTIFACT_STATUS_EXPIRED: "报告文件已过期",
        ARTIFACT_STATUS_MISSING: "报告文件缺失",
    }
    return labels.get(status, "报告文件状态未知")


def _render_artifact_status(row: dict, input_available: bool) -> None:
    status = str(row.get("artifact_status") or ARTIFACT_STATUS_MISSING)
    if status == ARTIFACT_STATUS_AVAILABLE:
        return
    if status == ARTIFACT_STATUS_EXPIRED:
        message = "报告文件已过期；历史索引已保留。"
    else:
        message = "报告文件缺失；历史索引已保留。"
    if input_available:
        message += " 原始输入仍存在，可重新识别。"
    else:
        message += " 原始输入也不可用，无法从该记录重新识别。"
    st.warning(message)


def _render_delete_recovery(repository: AnalysisRepository, row: dict, analysis_id: str) -> None:
    status = str(row.get("delete_status") or "")
    if status not in {"deleting", "delete_failed"}:
        return
    payload = _delete_payload_from_row(row)
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    deleted_paths = payload.get("deleted_paths") if isinstance(payload.get("deleted_paths"), list) else []
    st.warning(f"本地文件删除未完成，历史记录已保留。失败 {len(errors)} 个，已删除 {len(deleted_paths)} 个。")
    if errors:
        st.json({"errors": errors, "deleted_paths": deleted_paths, "skipped_paths": payload.get("skipped_paths") or []})
    if st.button("重试删除残留文件", key=f"history_retry_delete_files_{analysis_id}", type="primary"):
        _delete_analysis_and_files_with_notice(repository, analysis_id)
        st.rerun()


def _delete_payload_from_row(row: dict) -> dict:
    try:
        payload = json.loads(str(row.get("delete_errors") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _delete_analysis_and_files_with_notice(repository: AnalysisRepository, analysis_id: str) -> dict:
    result = repository.delete_analysis_and_files(analysis_id)
    deleted = len(result.get("deleted_paths") or [])
    errors = len(result.get("errors") or [])
    if errors:
        st.session_state["history_delete_warning"] = (
            f"本地文件删除失败，历史记录已保留；已删除 {deleted} 个路径，失败 {errors} 个。可重试删除残留文件。"
        )
        return result
    _clear_open_history_report(analysis_id)
    st.session_state["history_delete_notice"] = f"已删除历史索引和 {deleted} 个本地路径。"
    return result


def _rerun_analysis(repository: AnalysisRepository, row: dict, backend: str) -> None:
    input_path = Path(str(row.get("input_path") or "")).expanduser().resolve()
    try:
        run = create_image_run_from_file(input_path, original_filename=row.get("filename") or input_path.name)
        generator = MoleculeReportGenerator(backend, run.run_dir, runtime_config=runtime_config_from_key(current_runtime_key()))
        report = generator.generate(image_path=run.input_path, analysis_id=run.analysis_id)
        report_path = save_run_report(report, run)
        repository.save_analysis(report, report_path)
        st.session_state["history_report"] = report
        st.success("已完成重新识别。")
        st.rerun()
    except Exception as exc:
        st.warning(f"重新识别失败：{exc}")


def _reexport_analysis(repository: AnalysisRepository, analysis_id: str) -> None:
    report = repository.load_report(analysis_id)
    if not report:
        st.warning("报告文件不存在，无法重新导出。")
        return
    export_dir = OUTPUT_DIR / "history_exports" / analysis_id
    try:
        exports = export_structure_files(report, export_dir, prefix=f"history_{analysis_id[:8]}")
        record_report(report, repository.get_analysis(analysis_id).get("report_path") if repository.get_analysis(analysis_id) else None)
        st.success(f"已重新导出：{exports['zip']}")
    except Exception as exc:
        st.warning(f"重新导出失败：{exc}")


def _clear_open_history_report(analysis_id: str) -> None:
    if (st.session_state.get("history_report") or {}).get("analysis_id") == analysis_id:
        st.session_state.pop("history_report", None)


def _reports_zip(repository: AnalysisRepository, rows: list[dict]) -> bytes:
    buffer = BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            analysis_id = str(row.get("analysis_id") or "")
            report = repository.load_report(analysis_id)
            if not report:
                continue
            archive.writestr(f"{analysis_id}.json", to_json_text(report))
            count += 1
    return buffer.getvalue() if count else b""
