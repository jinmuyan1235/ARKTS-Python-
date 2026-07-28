"""Batch image processing page."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from src.analysis.correction import human_review_state, is_structure_confirmed
from src.export.json_exporter import to_json_text
from src.runtime.batch_inputs import batch_input_limits, batch_upload_previews, inspect_batch_uploads
from src.runtime.batch_job_store import BatchJobStore
from src.runtime.batch_result_review import apply_batch_review_actions, persist_batch_result
from src.runtime.job_registry import (
    cancel_batch_job,
    clear_batch_job,
    load_batch_job_result,
    refresh_batch_job,
    request_skip_current,
    pause_batch_job,
    resume_batch_job,
    start_batch_job,
    start_batch_job_from_uploads,
    start_batch_retry_job,
)
from src.storage.analysis_repository import AnalysisRepository, record_result_payload
from src.ui.labels import BATCH_COLUMN_LABELS, localize_batch_rows
from src.ui.records import render_records
from src.ui.state import (
    current_runtime_key,
    remember_backend_status,
    runtime_config_from_key,
)
from src.ui.styles import page_intro

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_COLUMNS = [
    "filename",
    "status",
    "backend",
    "final_smiles",
    "valid",
    "confidence",
    "inference_time_ms",
    "message",
]

RUNNING_BATCH_STATUSES = {"queued", "running", "paused", "cancelling"}


def render_batch_page(backend: str) -> None:
    page_intro("批量处理", "批量任务在后台运行；页面刷新后可以恢复进度和下载结果。")
    store = BatchJobStore()
    active_job = _active_job(store)
    running = bool(active_job and _is_running_batch_status(active_job.get("status")))
    uploaded_files = st.file_uploader(
        "上传多张图片或 ZIP",
        type=["png", "jpg", "jpeg", "zip"],
        accept_multiple_files=True,
        key="batch_upload",
        disabled=running,
    )
    folder_files = st.file_uploader(
        "导入图片文件夹",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files="directory",
        key="batch_folder_upload",
        disabled=running,
        help="浏览器会上传所选文件夹中的受支持图片；不会读取其他文件。",
    )
    uploads = [(item.name, item.getvalue()) for item in [*(uploaded_files or []), *(folder_files or [])]]
    inspection = inspect_batch_uploads(uploads) if uploads else None
    if inspection:
        _render_batch_input_preview(inspection, batch_upload_previews(uploads))

    with st.expander("高级选项", expanded=False):
        folder_path = st.text_input(
            "服务器本地文件夹路径",
            value="",
            disabled=running,
            help="仅适用于当前服务器能够访问的路径；普通使用请优先上传图片、ZIP 或文件夹。",
        )
        limits = batch_input_limits()
        st.caption(
            f"输入上限：{limits['max_files']} 张；单文件 {limits['max_file_size_mb']:g} MB；"
            f"总计 {limits['max_total_size_mb']:g} MB；启动前会检查图片格式、像素数和磁盘空间。"
        )

    invalid_upload = bool(inspection and inspection.get("errors"))
    if st.button(
        "开始后台批量任务",
        type="primary",
        key="analyze_batch",
        disabled=running or invalid_upload,
    ):
        launch_status = st.status("正在启动批量任务…", expanded=True)
        try:
            launch_status.write("正在校验输入并保存任务文件，请稍候。")
            runtime = runtime_config_from_key(current_runtime_key())
            if uploads:
                active_job = start_batch_job_from_uploads(uploads, backend, runtime, store=store)
            elif folder_path.strip():
                active_job = start_batch_job(folder_path.strip(), backend, runtime, store=store, source="folder")
            else:
                st.warning("请上传图片、ZIP、文件夹，或在高级选项中填写服务器路径。")
                launch_status.update(label="未启动：请先选择输入文件", state="error", expanded=True)
                active_job = None
            if active_job:
                st.session_state["batch_job_id"] = active_job["job_id"]
                st.session_state.pop("batch_result", None)
                remember_backend_status(backend)
                launch_status.update(label="任务已创建，正在加载进度…", state="complete", expanded=False)
                st.rerun()
        except Exception as exc:
            launch_status.update(label="启动批量任务失败", state="error", expanded=True)
            st.error(f"启动批量任务失败：{exc}")

    active_job = _active_job(store)
    if active_job:
        _index_job(active_job)
        if _is_running_batch_status(active_job.get("status")):
            _render_live_job_status(str(active_job["job_id"]), backend)
        else:
            _render_job_status(active_job, store, backend)
        batch_result = load_batch_job_result(active_job["job_id"], store)
        if batch_result:
            record_result_payload(batch_result, active_job.get("result_path"))
            st.session_state["batch_result"] = batch_result
    else:
        _render_restore_jobs(store)

    if "batch_result" not in st.session_state:
        return
    _render_batch_result(st.session_state["batch_result"], active_job, store, backend)


def _active_job(store: BatchJobStore) -> dict | None:
    job_id = st.session_state.get("batch_job_id")
    if not job_id or not store.exists(str(job_id)):
        return None
    try:
        return refresh_batch_job(str(job_id), store)
    except Exception as exc:
        st.warning(f"恢复批量任务失败：{exc}")
        st.session_state.pop("batch_job_id", None)
        return None


def _render_batch_input_preview(inspection: dict, previews: list[tuple[str, bytes]]) -> None:
    total = int(inspection.get("total_files") or 0)
    valid = int(inspection.get("valid_files") or 0)
    duplicates = int(inspection.get("duplicate_files") or 0)
    total_size = _human_size(int(inspection.get("total_bytes") or 0))
    st.markdown(
        f"**待处理文件：{total} 个**　·　有效 {valid}　·　重复 {duplicates}　·　总大小 {total_size}"
    )
    entries = list(inspection.get("entries") or [])
    with st.expander(f"查看文件清单（{len(entries)} 项）", expanded=False):
        for item in entries[:50]:
            valid_item = bool(item.get("valid"))
            status = "✅" if valid_item else "⚠️"
            dimensions = f"{item.get('width')}×{item.get('height')}" if item.get("width") else "-"
            duplicate = f" · 重复于 {item.get('duplicate_of')}" if item.get("duplicate_of") else ""
            message = "通过" if valid_item else str(item.get("message") or "失败")
            st.caption(
                f"{status} {item.get('name')} · {_human_size(int(item.get('size_bytes') or 0))} · "
                f"{item.get('format') or item.get('extension') or '-'} · {dimensions} · {message}{duplicate}"
            )
        if len(entries) > 50:
            st.caption(f"其余 {len(entries) - 50} 个文件已省略，任务仍会全部处理。")
    if previews:
        with st.expander(f"缩略图预览（前 {len(previews)} 张）", expanded=False):
            columns = st.columns(4)
            for index, (name, content) in enumerate(previews):
                with columns[index % 4]:
                    st.image(content, caption=name, width="stretch")
    errors = list(inspection.get("errors") or [])
    if errors:
        st.error("输入校验未通过：\n\n" + "\n\n".join(f"- {message}" for message in errors[:8]))
    elif inspection.get("duplicate_files"):
        st.info("检测到内容完全相同的图片；任务会保留文件记录，但通过 SHA-256 复用第一次识别结果。")


def _human_size(size_bytes: int) -> str:
    value = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _render_restore_jobs(store: BatchJobStore) -> None:
    jobs = store.list_jobs(limit=8)
    if not jobs:
        return
    with st.expander("恢复批量任务", expanded=False):
        labels = [_job_label(job) for job in jobs]
        selected = st.selectbox("选择历史任务", list(range(len(jobs))), format_func=lambda index: labels[index], key="restore_batch_job")
        if st.button("恢复所选任务", key="restore_batch_job_button"):
            st.session_state["batch_job_id"] = jobs[int(selected)]["job_id"]
            result = load_batch_job_result(jobs[int(selected)]["job_id"], store)
            if result:
                record_result_payload(result, jobs[int(selected)].get("result_path"))
                st.session_state["batch_result"] = result
            st.rerun()


def _render_job_status(job: dict, store: BatchJobStore, backend: str) -> None:
    status = str(job.get("status") or "unknown")
    total = int(job.get("total") or 0)
    completed = int(job.get("completed") or 0)
    st.subheader("后台任务状态")
    st.progress((completed / total) if total else 0.0)
    counts = _batch_status_counts(job)
    elapsed, remaining = _job_timing(job)
    metrics = st.columns(4)
    metrics[0].metric("总数", total)
    metrics[1].metric("已完成", completed)
    metrics[2].metric("成功", int(job.get("successful") or 0))
    metrics[3].metric("待确认", int(job.get("pending_confirmation") or counts["manual_review_total"]))
    second = st.columns(4)
    second[0].metric("拒识", counts["rejected"])
    second[1].metric("失败", counts["failed"])
    second[2].metric("已耗时", _duration_text(elapsed))
    second[3].metric("预计剩余", _duration_text(remaining) if remaining is not None else "计算中")
    st.caption(
        f"状态：{_status_label(status)}；"
        f"当前文件：{job.get('current_file') or '-'}；"
        f"缓存命中：{int(job.get('cache_hits') or 0)}；并发上限：{int(job.get('max_concurrency') or 1)}；"
        f"任务 ID：{job.get('job_id')}"
    )
    if job.get("error"):
        st.error(f"失败原因：{job.get('error')}")
    controls = st.columns(5)
    if controls[0].button("刷新状态", key="refresh_batch_job"):
        st.rerun()
    if controls[1].button("暂停", key="pause_batch_job", disabled=status != "running"):
        pause_batch_job(job["job_id"], store)
        st.rerun()
    can_continue = status in {"paused", "failed", "cancelled"}
    if controls[2].button("继续/断点续跑", key="resume_batch_job", disabled=not can_continue):
        try:
            resume_batch_job(job["job_id"], store)
            st.rerun()
        except Exception as exc:
            st.error(f"无法继续任务：{exc}")
    if controls[3].button("取消任务", key="cancel_batch_job", disabled=not _is_running_batch_status(status)):
        cancel_batch_job(job["job_id"], store)
        st.rerun()
    if controls[4].button(
        "跳过下一张未开始文件",
        key="skip_batch_next_unstarted",
        disabled=status != "running",
        help="不会中断正在推理的图片；请求会在下一张图片开始前生效。",
    ):
        request_skip_current(job["job_id"], store)
        st.rerun()
    result = load_batch_job_result(job["job_id"], store)
    followups = st.columns(3)
    if followups[0].button("重试失败项", key="retry_failed_batch", disabled=not result):
        _start_retry_job(result, backend, "failed", store, job["job_id"])
    if followups[1].button("只重试待确认项", key="retry_review_batch", disabled=not result):
        _start_retry_job(result, backend, "review", store, job["job_id"])
    if followups[2].button("清除任务", key="clear_batch_job", disabled=_is_running_batch_status(status)):
        clear_batch_job(job["job_id"], store)
        st.session_state.pop("batch_job_id", None)
        st.session_state.pop("batch_result", None)
        st.rerun()


@st.fragment(run_every="3s")
def _render_live_job_status(job_id: str, backend: str) -> None:
    store = BatchJobStore()
    try:
        job = refresh_batch_job(job_id, store)
    except Exception as exc:
        st.warning(f"恢复批量任务失败：{exc}")
        if st.session_state.get("batch_job_id") == job_id:
            st.session_state.pop("batch_job_id", None)
        st.rerun()
    _index_job(job)
    if _is_running_batch_status(job.get("status")):
        _render_job_status(job, store, backend)
        st.caption("任务运行中，状态区域每 3 秒自动刷新。")
        return
    result = load_batch_job_result(job_id, store)
    if result:
        record_result_payload(result, job.get("result_path"))
        st.session_state["batch_result"] = result
    st.rerun()


def _is_running_batch_status(status: object) -> bool:
    return str(status or "") in RUNNING_BATCH_STATUSES


def _job_timing(job: dict, now: datetime | None = None) -> tuple[float, float | None]:
    started = _parse_utc(job.get("started_at"))
    if started is None:
        return 0.0, None
    finished = _parse_utc(job.get("finished_at"))
    end = finished or now or datetime.now(timezone.utc)
    elapsed = max(0.0, (end - started).total_seconds())
    completed = int(job.get("completed") or 0)
    total = int(job.get("total") or 0)
    if completed <= 0 or total <= completed:
        return elapsed, 0.0 if total and total <= completed else None
    return elapsed, (elapsed / completed) * (total - completed)


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}时{minutes:02d}分" if hours else f"{minutes:d}分{remainder:02d}秒"


def _start_retry_job(result: dict, backend: str, mode: str, store: BatchJobStore, parent_job_id: str) -> None:
    try:
        retry = start_batch_retry_job(
            result,
            backend,
            mode,
            runtime_config_from_key(current_runtime_key()),
            store=store,
            parent_job_id=parent_job_id,
        )
        st.session_state["batch_job_id"] = retry["job_id"]
        st.session_state.pop("batch_result", None)
        st.rerun()
    except Exception as exc:
        st.warning(str(exc))


def _index_job(job: dict) -> None:
    try:
        AnalysisRepository().save_job(job, job_type="batch")
    except Exception:
        return


def _render_batch_result(batch_result: dict, job: dict | None, store: BatchJobStore, backend: str) -> None:
    batch_result = _ensure_batch_exports(batch_result, job, store)
    summary = batch_result["summary"]
    metrics = st.columns(4)
    metrics[0].metric("总图片", summary["total"])
    metrics[1].metric("已处理", summary.get("completed", summary["total"]))
    metrics[2].metric("待人工确认", summary.get("pending_confirmation", 0))
    metrics[3].metric("已确认", sum(is_structure_confirmed(report) for report in batch_result.get("reports") or []))
    _render_batch_status_metrics(summary)

    reports = list(batch_result.get("reports") or [])
    rows = list(batch_result.get("rows") or [])
    table_rows = []
    for index, report in enumerate(reports):
        row = rows[index] if index < len(rows) else {}
        input_data = report.get("input") or {}
        images = report.get("images") or {}
        review = human_review_state(report)
        table_rows.append({
            "original_path": input_data.get("path"),
            "candidate_path": images.get("redrawn_molecule"),
            "文件名": input_data.get("filename") or row.get("filename"),
            "候选 SMILES": row.get("candidate_smiles") or row.get("final_smiles") or "",
            "确认状态": "已确认" if review.get("confirmed") else "待确认",
            "识别状态": row.get("recognition_decision") or row.get("status"),
            "失败/拒识原因": row.get("message") or "",
            "分析 ID": report.get("analysis_id"),
        })
    st.subheader("结果表")
    _render_batch_result_rows(table_rows)
    if st.checkbox("查看完整字段", value=False, key="show_batch_full_fields"):
        render_records(
            localize_batch_rows(rows),
            title_keys=("文件名",),
            summary_keys=("状态", "识别后端", "最终 SMILES", "失败原因"),
            max_records=100,
        )

    if reports:
        _render_batch_review_controls(batch_result, reports, job, store, backend)

    chart = batch_result["exports"].get("summary_chart")
    if chart and Path(chart).is_file():
        st.image(chart, caption="批量结果统计", width=640)

    with st.expander("结果下载", expanded=True):
        st.warning("SMI、SDF 和结构 ZIP 仅包含已经人工确认的正式结构；未确认结果只保留在候选 CSV 和待确认清单中。")
        csv_path = Path(str(batch_result["exports"].get("csv") or ""))
        if csv_path.is_file():
            st.download_button("下载批量结果表 CSV", csv_path.read_bytes(), "batch_results.csv", "text/csv", key="batch_csv")
        _download_export_if_present(batch_result["exports"], "merged_sdf", "下载已确认结构 SDF", "chemical/x-mdl-sdfile", "batch_merged_sdf")
        _download_export_if_present(batch_result["exports"], "merged_smi", "下载已确认结构 SMI", "chemical/x-daylight-smiles", "batch_merged_smi")
        _download_export_if_present(batch_result["exports"], "complete_zip", "下载完整结果 ZIP", "application/zip", "batch_complete_zip")
        _download_export_if_present(batch_result["exports"], "failed_csv", "下载失败清单 CSV", "text/csv", "batch_failed_csv")
        _download_export_if_present(batch_result["exports"], "review_csv", "下载待确认清单 CSV", "text/csv", "batch_review_csv")
        with st.expander("高级导出", expanded=False):
            st.download_button(
                "下载完整 JSON",
                to_json_text({"summary": summary, "results": batch_result["reports"]}),
                "batch_results.json",
                "application/json",
                key="batch_json",
            )


def _render_batch_review_controls(
    batch_result: dict,
    reports: list[dict],
    job: dict | None,
    store: BatchJobStore,
    backend: str,
) -> None:
    st.subheader("批量确认与修正")
    report_by_id = {str(report.get("analysis_id")): report for report in reports}
    ids = list(report_by_id)
    labels = {
        analysis_id: str((report_by_id[analysis_id].get("input") or {}).get("filename") or analysis_id)
        for analysis_id in ids
    }
    selected_ids = st.multiselect(
        "批量选择结果",
        ids,
        format_func=lambda value: f"{labels[value]} · {value[:8]}",
        key="batch_review_selected_ids",
    )
    bulk = st.columns(2)
    if bulk[0].button("批量确认所选候选", disabled=not selected_ids, key="batch_confirm_selected"):
        _apply_and_persist_batch_actions(
            batch_result,
            [{"action": "confirm", "analysis_id": value} for value in selected_ids],
            job,
            store,
        )
    if bulk[1].button("重新识别所选文件", disabled=not selected_ids or job is None, key="batch_rerun_selected"):
        try:
            retry = start_batch_retry_job(
                batch_result,
                backend,
                "selected",
                runtime_config_from_key(current_runtime_key()),
                store=store,
                parent_job_id=str((job or {}).get("job_id") or ""),
                analysis_ids=selected_ids,
            )
            st.session_state["batch_job_id"] = retry["job_id"]
            st.session_state.pop("batch_result", None)
            st.rerun()
        except Exception as exc:
            st.error(f"重新识别启动失败：{exc}")

    current_id = st.selectbox(
        "当前审核结果",
        ids,
        format_func=lambda value: f"{labels[value]} · {value[:8]}",
        key="batch_current_review_id",
    )
    current = report_by_id[str(current_id)]
    input_data = current.get("input") or {}
    images = current.get("images") or {}
    preview = st.columns([0.24, 0.24, 0.52], gap="medium")
    if Path(str(input_data.get("path") or "")).is_file():
        preview[0].image(str(input_data["path"]), caption="原图", width=280)
    else:
        preview[0].info("原图文件不可用。")
    if Path(str(images.get("redrawn_molecule") or "")).is_file():
        preview[1].image(str(images["redrawn_molecule"]), caption="候选结构", width=280)
    else:
        preview[1].info("当前没有可显示的候选结构图。")
    final = current.get("final") or {}
    ocsr = current.get("ocsr") or {}
    candidate_smiles = str(final.get("smiles") or ocsr.get("smiles") or "")
    review = human_review_state(current)
    preview[2].markdown("**候选 SMILES**")
    preview[2].code(candidate_smiles or "暂无候选 SMILES", language=None)
    preview[2].caption(
        f"状态：{'已确认' if review.get('confirmed') else '待确认'}；"
        f"识别决策：{(current.get('recognition_decision') or {}).get('decision') or current.get('status')}；"
        f"原因：{current.get('message') or '-'}"
    )
    quality_reasons = set(str(item) for item in (current.get("image_quality") or {}).get("reason_codes") or [])
    if quality_reasons & {"blurred", "low_contrast"}:
        preview[2].warning("原图存在模糊或低对比度；系统会使用清晰增强图参与 OCSR，但候选结构仍需逐原子核对。")
    corrected = st.text_input("修正 SMILES", value=candidate_smiles, key=f"batch_correct_smiles_{current_id}")
    actions = st.columns(3)
    if actions[0].button("确认当前候选", disabled=not candidate_smiles or review.get("confirmed"), key=f"batch_confirm_{current_id}"):
        _apply_and_persist_batch_actions(batch_result, [{"action": "confirm", "analysis_id": current_id}], job, store)
    if actions[1].button("校验并应用修正", disabled=not corrected.strip(), key=f"batch_correct_{current_id}"):
        _apply_and_persist_batch_actions(
            batch_result,
            [{"action": "correct_smiles", "analysis_id": current_id, "smiles": corrected}],
            job,
            store,
        )
    if actions[2].button("撤销确认", disabled=not review.get("confirmed"), key=f"batch_revoke_{current_id}"):
        _apply_and_persist_batch_actions(batch_result, [{"action": "revoke", "analysis_id": current_id}], job, store)


def _apply_and_persist_batch_actions(
    batch_result: dict,
    actions: list[dict],
    job: dict | None,
    store: BatchJobStore,
) -> None:
    if not job or not job.get("result_path"):
        st.error("当前结果缺少持久化任务路径，无法保存审核操作。请先恢复对应批量任务。")
        return
    try:
        output_dir = Path(str(job.get("output_dir") or Path(str(job["result_path"])).parent))
        updated = apply_batch_review_actions(batch_result, actions, output_dir)
        result_path = persist_batch_result(updated, str(job["result_path"]))
        counts = _batch_status_counts(updated["summary"])
        store.update(
            str(job["job_id"]),
            result_path=str(result_path),
            exports=updated["exports"],
            summary=updated["summary"],
            pending_confirmation=int(updated["summary"].get("pending_confirmation") or 0),
            manual_review_total=counts["manual_review_total"],
            message="批量人工审核结果已保存。",
        )
        record_result_payload(updated, result_path)
        st.session_state["batch_result"] = updated
        st.success("审核操作已保存，正式导出文件已重新生成。")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


def _ensure_batch_exports(batch_result: dict, job: dict | None, store: BatchJobStore) -> dict:
    """Upgrade legacy batch payloads with confirmation-aware exports on first view."""
    exports = batch_result.get("exports") or {}
    required = ("csv", "merged_sdf", "merged_smi", "complete_zip", "failed_csv", "review_csv")
    if all(exports.get(field) and Path(str(exports[field])).is_file() for field in required):
        return batch_result
    if not job or not job.get("result_path"):
        return batch_result
    try:
        output_dir = Path(str(job.get("output_dir") or Path(str(job["result_path"])).parent))
        updated = apply_batch_review_actions(batch_result, [], output_dir)
        result_path = persist_batch_result(updated, str(job["result_path"]))
        store.update(
            str(job["job_id"]),
            result_path=str(result_path),
            exports=updated["exports"],
            summary=updated["summary"],
            pending_confirmation=int(updated["summary"].get("pending_confirmation") or 0),
        )
        st.session_state["batch_result"] = updated
        return updated
    except Exception as exc:
        st.warning(f"旧批量报告的新版导出文件暂时无法生成：{exc}")
        return batch_result


def _render_batch_result_rows(rows: list[dict], max_rows: int = 100) -> None:
    """Render image-rich batch rows without pandas/PyArrow native table components."""
    if not rows:
        st.info("当前任务还没有可展示的结果。")
        return
    for row in rows[:max_rows]:
        with st.container(border=True):
            columns = st.columns([0.13, 0.13, 0.18, 0.28, 0.12, 0.16], gap="small")
            original = Path(str(row.get("original_path") or ""))
            candidate = Path(str(row.get("candidate_path") or ""))
            if original.is_file():
                columns[0].image(str(original), caption="原图", width="stretch")
            else:
                columns[0].caption("原图不可用")
            if candidate.is_file():
                columns[1].image(str(candidate), caption="候选结构", width="stretch")
            else:
                columns[1].caption("无候选结构图")
            columns[2].markdown(f"**文件名**  \n{row.get('文件名') or '-'}")
            columns[3].markdown(f"**候选 SMILES**  \n`{row.get('候选 SMILES') or '-'}`")
            columns[4].markdown(
                f"**确认状态**  \n{row.get('确认状态') or '-'}  \n"
                f"**识别状态**  \n{row.get('识别状态') or '-'}"
            )
            columns[5].markdown(f"**失败/拒识原因**  \n{row.get('失败/拒识原因') or '-'}")
    remaining = len(rows) - min(len(rows), max_rows)
    if remaining > 0:
        st.caption(f"还有 {remaining} 条结果未展开，请使用 CSV 查看完整记录。")


def _download_export_if_present(exports: dict, field: str, label: str, mime: str, key: str) -> None:
    path_value = exports.get(field)
    if not path_value:
        return
    path = Path(path_value)
    if not path.is_file():
        return
    st.download_button(label, path.read_bytes(), path.name, mime, key=key)


def _render_batch_status_metrics(summary: dict) -> None:
    counts = _batch_status_counts(summary)
    metrics = st.columns(7)
    metrics[0].metric("自动接受", counts["accepted"])
    metrics[1].metric("警告待确认", counts["accepted_with_warning"])
    metrics[2].metric("明确需要审核", counts["review_needed"])
    metrics[3].metric("拒绝", counts["rejected"])
    metrics[4].metric("失败", counts["failed"])
    metrics[5].metric("跳过", counts["skipped"])
    metrics[6].metric("人工审核总数", counts["manual_review_total"])


def _batch_status_counts(summary: dict) -> dict[str, int]:
    accepted = int(summary.get("accepted") or 0)
    accepted_with_warning = int(summary.get("accepted_with_warning") or 0)
    review_needed = int(summary.get("review_needed") or 0)
    rejected = int(summary.get("rejected") or 0)
    failed = int(summary.get("failed") or 0)
    skipped = int(summary.get("skipped") or 0)
    schema_version = int(summary.get("summary_schema_version") or 0)
    if "manual_review_total" in summary or schema_version >= 2:
        manual_review_total = int(summary.get("manual_review_total") or (accepted_with_warning + review_needed))
    else:
        manual_review_total = review_needed
        review_needed = max(0, review_needed - accepted_with_warning)
    return {
        "accepted": accepted,
        "accepted_with_warning": accepted_with_warning,
        "review_needed": review_needed,
        "manual_review_total": manual_review_total,
        "rejected": rejected,
        "failed": failed,
        "skipped": skipped,
    }


def default_batch_columns_chinese() -> list[str]:
    return [BATCH_COLUMN_LABELS[column] for column in DEFAULT_COLUMNS]


def _job_label(job: dict) -> str:
    return (
        f"{_status_label(str(job.get('status')))} | "
        f"{job.get('completed', 0)}/{job.get('total', 0)} | "
        f"{job.get('backend')} | {job.get('updated_at') or job.get('created_at')}"
    )


def _status_label(status: str) -> str:
    return {
        "queued": "排队中",
        "running": "运行中",
        "paused": "已暂停",
        "cancelling": "取消中",
        "cancelled": "已取消",
        "completed": "已完成",
        "failed": "失败",
    }.get(status, status)
