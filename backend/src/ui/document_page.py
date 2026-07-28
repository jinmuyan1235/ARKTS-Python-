"""PDF and multi-molecule document page."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components
import cv2
import numpy as np

import config
from src.documents.input_loader import DocumentInputError, OptionalDependencyError
from src.documents.processor import DocumentOCSRProcessor
from src.documents.region_review import (
    apply_canvas_event,
    background_failure_reason,
    canvas_event_from_query,
    persist_document_result_atomic,
    save_region_selection,
)
from src.storage.analysis_repository import record_result_payload
from src.ui.image_viewer import show_document_page
from src.ui.labels import REGION_TYPE_LABELS, localize_region_rows, region_type_label, status_label
from src.ui.records import render_records
from src.ui.state import current_runtime_key, get_document_processor, remember_backend_status, runtime_config_from_key
from src.ui.styles import page_intro
from src.runtime.job_manager import (
    extract_json_object,
    is_process_alive,
    run_json_command,
    start_logged_process,
    terminate_process_tree,
    terminate_process_tree_by_pid,
)

DOCUMENT_WORKFLOW_LABEL = "全文检测与审核识别"
DOCUMENT_PROGRESS_MARKER = "DOCUMENT_PROGRESS_JSON="
DOCUMENT_RESULT_MARKER = "DOCUMENT_RESULT_JSON="
REGION_PROGRESS_MARKER = "DOCUMENT_REGION_PROGRESS_JSON="
CURRENT_DOCUMENT_SCREENING_SUFFIX = "-v3"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_REGION_TYPES = ["molecule", "text", "table", "reaction", "ignore"]
DOCUMENT_HISTORY_LIMIT = 20
DOCUMENT_BBOX_QUERY_KEYS = (
    "document_region_editor_key",
    "doc_bbox_action",
    "doc_bbox_region_id",
    "doc_bbox_page",
    "doc_bbox_x1",
    "doc_bbox_y1",
    "doc_bbox_x2",
    "doc_bbox_y2",
    "doc_canvas_width",
    "doc_canvas_height",
    "doc_bbox_nonce",
)


def render_document_page(backend: str) -> None:
    page_intro("PDF/多分子文档", "一个入口处理整篇论文：全文分页、区域检测、人工审核和后台 OCSR 识别。")
    _restore_region_job_from_disk()
    upload = st.file_uploader(
        "上传 PDF / 页面图片 / ZIP",
        type=["pdf", "png", "jpg", "jpeg", "zip"],
        key="document_upload",
    )
    st.info(
        "综合流程：先检测整篇文档的分子候选框，再在同一审核台中修正，并在确认后启动识别；"
        "新检测框不会在人工确认前自动送入 OCSR。"
    )
    st.caption(
        f"当前支持一次处理最多 {config.DOCUMENT_MAX_PAGES} 页、{config.DOCUMENT_MAX_REGIONS} 个区域、"
        f"{config.DOCUMENT_MAX_FILE_SIZE_MB:g} MB 文件；上限均可通过环境变量调整。"
    )

    if upload is not None and st.button(f"开始{DOCUMENT_WORKFLOW_LABEL}", type="primary", key="process_document"):
        try:
            if backend != "demo":
                input_path = _persist_uploaded_document(upload)
                _start_document_job(input_path, backend, False)
                st.session_state.pop("document_result", None)
                st.rerun()
            else:
                _run_demo_document(upload, backend, False)
        except OptionalDependencyError as exc:
            st.error(str(exc))
        except (DocumentInputError, FileNotFoundError, RuntimeError, ValueError) as exc:
            st.error(str(exc))

    _render_document_job_status()
    _render_document_job_retry()
    if "document_result" not in st.session_state and st.session_state.get("document_region_job"):
        _render_region_ocsr_job_status(None)
    if "document_result" in st.session_state:
        st.session_state["document_result"] = show_document_result(st.session_state["document_result"], backend)
    elif not st.session_state.get("document_job"):
        _render_document_recovery()


def _run_demo_document(upload: Any, backend: str, run_ocsr: bool) -> None:
    suffix = Path(upload.name).suffix.lower()
    prefix = Path(upload.name).stem + "_"
    with tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=False) as temporary:
        temporary.write(upload.getvalue())
        temporary_path = Path(temporary.name)
    try:
        progress_text = st.empty()
        progress_bar = st.progress(0.0)

        def progress_callback(current: int, total: int, region_id: str) -> None:
            progress_text.info(f"正在识别第 {current}/{total} 个候选区域：{region_id}")
            progress_bar.progress(current / max(total, 1))

        def document_progress(stage: str, current: int, total: int, detail: str) -> None:
            progress_text.info(detail)
            if stage == "rendered":
                progress_bar.progress(0.2)
            elif stage == "detecting":
                progress_bar.progress(0.2 + 0.75 * current / max(total, 1))

        with st.spinner("正在渲染页面并检测区域……"):
            result = get_document_processor(backend).process(
                temporary_path,
                run_ocsr=run_ocsr,
                progress_callback=progress_callback if run_ocsr else None,
                document_progress_callback=document_progress,
            )
            record_result_payload(result, (result.get("exports") or {}).get("json"))
            st.session_state["document_result"] = result
            remember_backend_status(backend)
        progress_text.empty()
        progress_bar.empty()
    finally:
        temporary_path.unlink(missing_ok=True)


def _persist_uploaded_document(upload: Any) -> Path:
    job_dir = config.OUTPUT_DIR / "ui_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.name).name.replace(" ", "_")
    path = job_dir / f"document_{uuid4().hex}_{safe_name}"
    path.write_bytes(upload.getvalue())
    return path


def _document_command(input_path: Path, backend: str, run_ocsr: bool) -> list[str]:
    runtime = runtime_config_from_key(current_runtime_key())
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "process_document.py"),
        "--input",
        str(input_path),
        "--backend",
        backend,
    ]
    if not run_ocsr:
        command.append("--detect-only")
    if runtime.get("molscribe_device"):
        command.extend(["--molscribe-device", str(runtime["molscribe_device"])])
    if runtime.get("decimer_device"):
        command.extend(["--decimer-device", str(runtime["decimer_device"])])
    if runtime.get("visible_gpu_index") is not None:
        command.extend(["--visible-gpu-index", str(runtime["visible_gpu_index"])])
    return command


def _start_document_job(input_path: Path, backend: str, run_ocsr: bool) -> None:
    job_dir = config.OUTPUT_DIR / "ui_jobs" / uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    env = os.environ.copy()
    env.setdefault("MOLSCRIBE_ISOLATED_SUBPROCESS", "true")
    env.setdefault("DECIMER_ISOLATED_SUBPROCESS", "true")
    process = start_logged_process(
        _document_command(input_path, backend, run_ocsr),
        cwd=PROJECT_ROOT,
        env=env,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    st.session_state["document_job"] = {
        "process": process,
        "backend": backend,
        "input_path": str(input_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "started_at": time.time(),
    }


def _render_document_job_status() -> None:
    job = st.session_state.get("document_job")
    if not job:
        return
    process = job["process"]
    elapsed = time.time() - float(job.get("started_at", time.time()))
    return_code = process.poll()
    if return_code is None:
        stdout_path = Path(str(job.get("stdout_path") or ""))
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
        progress = _extract_document_progress(stdout)
        if progress:
            current = int(progress.get("current") or 0)
            total = max(1, int(progress.get("total") or 1))
            stage = str(progress.get("stage") or "")
            if stage == "rendered":
                fraction = 0.2
            elif stage == "detecting":
                fraction = 0.2 + 0.75 * current / total
            else:
                fraction = min(0.95, current / total)
            detail = str(progress.get("detail") or f"{current}/{total}")
            st.info(f"{DOCUMENT_WORKFLOW_LABEL}正在后台运行：{detail}")
            st.progress(min(0.95, fraction))
        else:
            st.info(f"正在渲染整篇文档，已耗时 {elapsed:.1f} 秒。完成分页后将自动开始逐页区域检测。")
            st.progress(min(0.15, elapsed / 180.0))
        st.caption("页面会自动刷新；全文任务在后台运行，不会让 Streamlit 无反馈等待。")
        st.caption(f"后台日志：{job.get('stdout_path')} / {job.get('stderr_path')}")
        if st.button("取消文档后台任务", key="cancel_document_job"):
            terminate_process_tree(process)
            st.session_state["document_job_retry"] = {key: value for key, value in job.items() if key != "process"}
            st.session_state.pop("document_job", None)
            st.warning("已取消文档处理任务并终止后台进程；可选择重试。")
            return
        time.sleep(2)
        st.rerun()

    stdout_path = Path(job["stdout_path"])
    stderr_path = Path(job["stderr_path"])
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.is_file() else ""
    payload = _extract_json_object(stdout)
    input_path = Path(str(job.get("input_path") or ""))
    st.session_state.pop("document_job", None)

    if return_code != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"文档处理子进程退出码 {return_code}"
        if payload and payload.get("message"):
            message = str(payload["message"])
        st.session_state["document_job_retry"] = {key: value for key, value in job.items() if key != "process"}
        st.error(f"文档处理失败：{message}")
        return
    if not payload or not payload.get("result_path"):
        st.session_state["document_job_retry"] = {key: value for key, value in job.items() if key != "process"}
        st.error("文档处理完成，但没有返回结果文件路径。")
        return
    result_path = Path(str(payload["result_path"]))
    if not result_path.is_file():
        st.session_state["document_job_retry"] = {key: value for key, value in job.items() if key != "process"}
        st.error(f"文档处理结果文件不存在：{result_path}")
        return
    if input_path.exists():
        input_path.unlink(missing_ok=True)
    st.session_state.pop("document_job_retry", None)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    record_result_payload(result, result_path)
    st.session_state["document_result"] = result
    st.session_state["document_job_logs"] = {
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    st.success("文档处理完成。")
    st.rerun()


def _apply_document_edits_subprocess(
    document_result: dict,
    backend: str,
    edits: list[dict],
    rerun_ocsr: bool,
) -> dict:
    output_dir = Path(document_result.get("output_dir") or tempfile.gettempdir()).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    current_path = output_dir / "document_result_for_edit.json"
    current_path.write_text(json.dumps(document_result, ensure_ascii=False, indent=2), encoding="utf-8")
    command = _document_edit_command(current_path, backend, edits, rerun_ocsr)

    env = os.environ.copy()
    env.setdefault("MOLSCRIBE_ISOLATED_SUBPROCESS", "true")
    env.setdefault("DECIMER_ISOLATED_SUBPROCESS", "true")
    completed = run_json_command(command, cwd=PROJECT_ROOT, env=env, timeout=900)
    payload = completed.payload
    if completed.timed_out:
        raise RuntimeError("文档编辑子进程超时，已终止后台进程。")
    if completed.returncode != 0:
        message = completed.last_output_line() or f"文档编辑子进程退出码 {completed.returncode}"
        if payload and payload.get("message"):
            message = str(payload["message"])
        raise RuntimeError(message)
    if not payload or not payload.get("result_path"):
        raise RuntimeError("文档编辑子进程未返回结果文件路径。")
    result_path = Path(str(payload["result_path"]))
    if not result_path.is_file():
        raise RuntimeError(f"文档编辑结果文件不存在：{result_path}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _document_edit_command(current_path: Path, backend: str, edits: list[dict], rerun_ocsr: bool) -> list[str]:
    runtime = runtime_config_from_key(current_runtime_key())
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "process_document_edit.py"),
        "--document-result",
        str(current_path),
        "--edits-json",
        json.dumps(edits, ensure_ascii=False),
        "--backend",
        backend,
    ]
    if rerun_ocsr:
        command.append("--rerun-ocsr")
    if runtime.get("molscribe_device"):
        command.extend(["--molscribe-device", str(runtime["molscribe_device"])])
    if runtime.get("decimer_device"):
        command.extend(["--decimer-device", str(runtime["decimer_device"])])
    if runtime.get("visible_gpu_index") is not None:
        command.extend(["--visible-gpu-index", str(runtime["visible_gpu_index"])])
    return command


def _write_region_job_state(job: dict, status: str, **extra: Any) -> None:
    state_path = Path(str(job.get("state_path") or ""))
    if not str(state_path) or state_path == Path("."):
        return
    payload = {
        "status": status,
        "pid": int(job.get("pid") or getattr(job.get("process"), "pid", 0) or 0),
        "region_id": str(job.get("region_id") or ""),
        "region_ids": [str(value) for value in (job.get("region_ids") or [])],
        "scope_label": str(job.get("scope_label") or ""),
        "page_number": int(job.get("page_number") or 0),
        "page_numbers": [int(value) for value in (job.get("page_numbers") or [])],
        "stdout_path": str(job.get("stdout_path") or ""),
        "stderr_path": str(job.get("stderr_path") or ""),
        "started_at": float(job.get("started_at") or time.time()),
        "backend": str(job.get("backend") or ""),
        "state_path": str(state_path),
        **extra,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _poll_region_job(job: dict) -> int | None:
    process = job.get("process")
    if process is not None:
        return process.poll()
    pid = int(job.get("pid") or 0)
    if is_process_alive(pid):
        return None
    stdout_path = Path(str(job.get("stdout_path") or ""))
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
    payload = _extract_json_object(stdout)
    return 0 if payload and payload.get("result_path") else 1


def _restore_region_job_from_disk() -> None:
    if st.session_state.get("document_region_job"):
        return
    root = config.OUTPUT_DIR / "ui_jobs"
    if not root.is_dir():
        return
    states = sorted(root.glob("region*/job_state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for state_path in states[:20]:
        try:
            job = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("status") != "running":
            continue
        job["process"] = None
        job["state_path"] = str(state_path)
        st.session_state["document_region_job"] = job
        return


def _start_region_ocsr_job(document_result: dict, backend: str, region_id: str, bbox: list[int]) -> dict:
    current_job = st.session_state.get("document_region_job")
    if current_job and _poll_region_job(current_job) is None:
        raise RuntimeError("已有区域识别任务正在运行，请等待其完成。")
    saved = save_region_selection(document_result, region_id, bbox, recognize=True)
    _push_document_history(document_result)
    current_path = persist_document_result_atomic(saved)
    record_result_payload(saved, current_path)
    selected = next(region for region in saved.get("regions", []) if str(region.get("region_id")) == str(region_id))
    job_dir = config.OUTPUT_DIR / "ui_jobs" / f"region_{uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    env = os.environ.copy()
    env.setdefault("MOLSCRIBE_ISOLATED_SUBPROCESS", "true")
    env.setdefault("DECIMER_ISOLATED_SUBPROCESS", "true")
    process = start_logged_process(
        _document_edit_command(
            current_path,
            backend,
            [{"action": "recognize", "region_id": region_id, "note": "Background OCSR requested by reviewer."}],
            True,
        ),
        cwd=PROJECT_ROOT,
        env=env,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    st.session_state["document_result"] = saved
    job = {
        "process": process,
        "pid": process.pid,
        "backend": backend,
        "region_id": str(region_id),
        "region_ids": [str(region_id)],
        "scope_label": f"区域 {region_id} 识别",
        "page_number": int(selected.get("page_number") or 0),
        "page_numbers": [int(selected.get("page_number") or 0)],
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "state_path": str(job_dir / "job_state.json"),
        "started_at": time.time(),
    }
    st.session_state["document_region_job"] = job
    _write_region_job_state(job, "running")
    return saved


def _start_confirmed_region_batch_job(
    document_result: dict,
    backend: str,
    region_ids: list[str],
    *,
    scope_label: str,
    page_number: int,
) -> dict:
    current_job = st.session_state.get("document_region_job")
    if current_job and _poll_region_job(current_job) is None:
        raise RuntimeError("已有区域识别任务正在运行，请等待其完成。")
    requested = {str(region_id) for region_id in region_ids}
    targets = [
        region for region in document_result.get("regions", [])
        if str(region.get("region_id")) in requested
        and region.get("status") != "deleted"
        and region.get("region_type") == "molecule"
        and bool(region.get("confirmed"))
    ]
    if not targets:
        raise ValueError("当前范围没有已确认的分子区域。请先逐个核对并保存结构框。")
    target_ids = [str(region.get("region_id")) for region in targets]
    current_path = persist_document_result_atomic(document_result)
    record_result_payload(document_result, current_path)
    job_dir = config.OUTPUT_DIR / "ui_jobs" / f"region_batch_{uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    env = os.environ.copy()
    env.setdefault("MOLSCRIBE_ISOLATED_SUBPROCESS", "true")
    env.setdefault("DECIMER_ISOLATED_SUBPROCESS", "true")
    process = start_logged_process(
        _document_edit_command(
            current_path,
            backend,
            [
                {"action": "recognize", "region_id": region_id, "note": f"{scope_label} background OCSR."}
                for region_id in target_ids
            ],
            True,
        ),
        cwd=PROJECT_ROOT,
        env=env,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    restore_id = str(st.session_state.get("document_region_select") or target_ids[0])
    target_pages = sorted({int(region.get("page_number") or 0) for region in targets})
    job = {
        "process": process,
        "pid": process.pid,
        "backend": backend,
        "region_id": restore_id,
        "region_ids": target_ids,
        "scope_label": scope_label,
        "page_number": int(page_number),
        "page_numbers": target_pages,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "state_path": str(job_dir / "job_state.json"),
        "started_at": time.time(),
    }
    st.session_state["document_region_job"] = job
    _write_region_job_state(job, "running")
    return document_result


@st.fragment(run_every="2s")
def _render_region_ocsr_job_status(inline_region_id: str | None = None) -> None:
    job = st.session_state.get("document_region_job")
    if not job:
        return
    process = job.get("process")
    elapsed = time.time() - float(job.get("started_at", time.time()))
    return_code = _poll_region_job(job)
    region_id = str(job.get("region_id") or "")
    region_ids = [str(value) for value in (job.get("region_ids") or [region_id])]
    scope_label = str(job.get("scope_label") or f"区域 {region_id}")
    if return_code is None:
        stdout_path = Path(str(job.get("stdout_path") or ""))
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
        progress = _extract_region_progress(stdout) or {}
        current = int(progress.get("current") or 0)
        total = max(1, int(progress.get("total") or len(region_ids) or 1))
        current_region = str(progress.get("region_id") or "等待模型加载")
        stage = "正在识别" if progress.get("stage") == "recognizing" else "正在保存结果"
        page_count = len(job.get("page_numbers") or [job.get("page_number")])
        target_note = "当前区域" if len(region_ids) == 1 and str(inline_region_id or "") in region_ids else scope_label
        with st.status(f"{target_note}正在后台识别…", state="running", expanded=True):
            st.write(f"页数：{page_count}；区域数：{len(region_ids)}；当前阶段：{stage}。")
            st.write(f"当前区域：{current_region}；进度：{current}/{total}。")
            fraction = current / total if current else min(0.12, elapsed / 180.0)
            st.progress(min(0.95, max(0.04, fraction)))
            st.caption(f"已运行 {elapsed:.1f} 秒。完成后会恢复当前页和当前区域。")
            if st.button("取消当前识别任务", key=f"cancel_region_job_{job.get('pid')}"):
                if process is not None:
                    terminate_process_tree(process)
                else:
                    terminate_process_tree_by_pid(int(job.get("pid") or 0))
                _write_region_job_state(job, "cancelled", elapsed_seconds=round(elapsed, 3))
                st.session_state["document_region_retry"] = {key: value for key, value in job.items() if key != "process"}
                st.session_state.pop("document_region_job", None)
                st.session_state["document_region_notice"] = {
                    "level": "error",
                    "message": f"{scope_label}已取消；可以使用“重试最近任务”重新启动。",
                }
                st.rerun()
        return

    stdout_path = Path(str(job.get("stdout_path") or ""))
    stderr_path = Path(str(job.get("stderr_path") or ""))
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.is_file() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.is_file() else ""
    payload = _extract_json_object(stdout)
    st.session_state.pop("document_region_job", None)
    st.session_state["document_region_restore"] = {
        "region_id": region_id,
        "page_number": str(job.get("page_number") or "全部"),
    }
    st.session_state["document_region_job_logs"] = {
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    if return_code != 0:
        reason = background_failure_reason(return_code, payload, stdout, stderr)
        _write_region_job_state(job, "failed", failure_reason=reason, elapsed_seconds=round(elapsed, 3))
        st.session_state["document_region_retry"] = {key: value for key, value in job.items() if key != "process"}
        st.session_state["document_region_notice"] = {"level": "error", "message": f"{scope_label}失败：{reason}"}
        st.rerun()
        return
    result_path_value = (payload or {}).get("result_path")
    result_path = Path(str(result_path_value or ""))
    if not result_path_value or not result_path.is_file():
        _write_region_job_state(job, "failed", failure_reason="结果文件缺失", elapsed_seconds=round(elapsed, 3))
        st.session_state["document_region_retry"] = {key: value for key, value in job.items() if key != "process"}
        st.session_state["document_region_notice"] = {
            "level": "error",
            "message": f"{scope_label}完成，但结果文件缺失。请查看后台日志。",
        }
        st.rerun()
        return
    updated = json.loads(result_path.read_text(encoding="utf-8"))
    current_result = st.session_state.get("document_result")
    if isinstance(current_result, dict):
        _push_document_history(current_result)
    st.session_state["document_result"] = updated
    _write_region_job_state(job, "completed", result_path=str(result_path), elapsed_seconds=round(elapsed, 3))
    st.session_state.pop("document_region_retry", None)
    record_result_payload(updated, result_path)
    result_regions = [item for item in updated.get("regions", []) if str(item.get("region_id")) in set(region_ids)]
    recognized_count = sum(str(item.get("status")) == "recognized" for item in result_regions)
    if len(region_ids) > 1:
        failed_count = len(region_ids) - recognized_count
        message = f"{scope_label}完成：成功 {recognized_count} 个，未成功 {failed_count} 个。"
        level = "success" if recognized_count else "error"
    else:
        region = result_regions[0] if result_regions else {}
        if str(region.get("status")) == "recognized":
            message = f"区域 {region_id} 识别成功，候选 SMILES 和重绘结构已更新。"
            level = "success"
        else:
            message = f"区域 {region_id} 识别未成功：{region.get('message') or '模型未返回可用结构。'}"
            level = "error"
    st.session_state["document_region_notice"] = {"level": level, "message": message}
    st.rerun()


def _render_document_job_retry() -> None:
    retry = st.session_state.get("document_job_retry")
    if not retry or st.session_state.get("document_job"):
        return
    input_path = Path(str(retry.get("input_path") or ""))
    if not input_path.is_file():
        st.session_state.pop("document_job_retry", None)
        return
    controls = st.columns([0.2, 0.2, 0.6])
    if controls[0].button("重试文档处理", key="retry_document_job", type="primary"):
        _start_document_job(input_path, str(retry.get("backend") or config.OCSR_BACKEND), False)
        st.session_state.pop("document_job_retry", None)
        st.rerun()
    if controls[1].button("放弃重试", key="dismiss_document_job_retry"):
        input_path.unlink(missing_ok=True)
        st.session_state.pop("document_job_retry", None)
        st.rerun()
    controls[2].caption(f"保留了原始上传文件，可直接重新启动：{input_path.name}")


def _apply_document_edits(document_result: dict, backend: str, edits: list[dict], rerun_ocsr: bool) -> dict:
    if backend == "demo":
        processor = get_document_processor(backend)
        updated = processor.apply_edits(document_result, edits, rerun_ocsr=rerun_ocsr)
    else:
        updated = _apply_document_edits_subprocess(document_result, backend, edits, rerun_ocsr)
    record_result_payload(updated, (updated.get("exports") or {}).get("json"))
    return updated


def _extract_json_object(text: str) -> dict | None:
    marked = extract_json_object(text, marker=DOCUMENT_RESULT_MARKER)
    if marked is not None and marked.get("result_path"):
        return marked
    decoder = json.JSONDecoder()
    fallback: dict | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if value.get("result_path"):
            return value
        fallback = value
    return fallback or extract_json_object(text)


def _extract_document_progress(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if not line.startswith(DOCUMENT_PROGRESS_MARKER):
            continue
        try:
            payload = json.loads(line[len(DOCUMENT_PROGRESS_MARKER) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _extract_region_progress(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if not line.startswith(REGION_PROGRESS_MARKER):
            continue
        try:
            payload = json.loads(line[len(REGION_PROGRESS_MARKER) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _latest_recoverable_document_result(jobs_root: str | Path) -> tuple[dict, Path] | None:
    root = Path(jobs_root)
    if not root.is_dir():
        return None
    logs = sorted(root.rglob("stdout.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    for log_path in logs[:20]:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        payload = _extract_json_object(text)
        result_path_value = (payload or {}).get("result_path")
        if not result_path_value:
            continue
        result_path = Path(str(result_path_value))
        if result_path.is_file():
            return payload or {}, result_path
    return None


def _render_document_recovery() -> None:
    recoverable = _latest_recoverable_document_result(config.OUTPUT_DIR / "ui_jobs")
    if recoverable is None:
        return
    payload, result_path = recoverable
    summary = payload.get("summary") or {}
    with st.expander("恢复最近完成的全文任务", expanded=False):
        st.caption(
            f"检测到可恢复结果：{summary.get('page_count', '-')} 页、"
            f"{summary.get('region_count', '-')} 个区域。无需重新处理 PDF。"
        )
        if st.button("恢复到审核台", key="recover_latest_document_result"):
            result = json.loads(result_path.read_text(encoding="utf-8"))
            st.session_state["document_result"] = result
            record_result_payload(result, result_path)
            st.success("最近完成的全文任务已恢复。")
            st.rerun()


def show_document_result(document_result: dict, backend: str) -> dict:
    if _document_needs_screening_refresh(document_result):
        try:
            with st.spinner("正在用最新规则重新筛选旧任务区域（不会运行 OCSR）……"):
                processor = get_document_processor(backend)
                rescreen = getattr(processor, "rescreen_document_result", None)
                if not callable(rescreen):
                    processor = DocumentOCSRProcessor(
                        backend=backend,
                        runtime_config=runtime_config_from_key(current_runtime_key()),
                    )
                    rescreen = processor.rescreen_document_result
                document_result = rescreen(document_result)
                result_path = persist_document_result_atomic(document_result)
                record_result_payload(document_result, result_path)
                st.session_state["document_result"] = document_result
                refresh = (document_result.get("processing") or {}).get("screening_refresh") or {}
                st.session_state["document_region_notice"] = {
                    "level": "success",
                    "message": (
                        f"已按最新规则重新筛选 {refresh.get('checked_region_count', 0)} 个区域，"
                        f"修正 {refresh.get('changed_region_count', 0)} 个区域类型；未运行 OCSR。"
                    ),
                }
            st.rerun()
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            st.warning(f"旧任务自动重新筛选失败：{exc}")
    notice = st.session_state.pop("document_region_notice", None)
    if notice:
        renderer = st.success if notice.get("level") == "success" else st.error
        renderer(str(notice.get("message") or "区域任务状态已更新。"))
    _render_region_retry_control(document_result, backend)
    summary = document_result.get("summary") or {}
    st.subheader("文档区域识别结果")
    metrics = st.columns(6)
    metrics[0].metric("页数", summary.get("page_count", 0))
    metrics[1].metric("检测区域", summary.get("region_count", 0))
    metrics[2].metric("已确认区域", summary.get("confirmed_region_count", 0))
    metrics[3].metric("分子候选区域", summary.get("molecule_region_count", 0))
    metrics[4].metric("识别成功", summary.get("recognized_region_count", 0))
    metrics[5].metric("入审核队列", summary.get("review_queue_count", 0))
    processing = document_result.get("processing") or {}
    if processing.get("total_time_ms") is not None:
        st.caption(f"文档处理总耗时：{float(processing.get('total_time_ms') or 0) / 1000:.2f} 秒")
    if document_result.get("detection_errors"):
        with st.expander("检测提示", expanded=False):
            st.json(document_result.get("detection_errors"))

    annotated = [item for item in (document_result.get("exports", {}).get("annotated_pages") or "").split(",") if item]
    if annotated and st.checkbox("显示区域标注预览", value=False, key="show_document_annotation_preview"):
        st.subheader("区域标注预览")
        page_names = [Path(path).name for path in annotated]
        selected_name = st.selectbox("选择页码", page_names, key="annotated_page_select")
        selected_path = annotated[page_names.index(selected_name)]
        show_document_page(selected_path, selected_name)

    rows = DocumentOCSRProcessor.region_rows(document_result)
    if not rows:
        st.info("未检测到分子候选区域。可以在下方手动添加区域。")
    document_result = _document_workbench(document_result, backend, rows)

    logs = st.session_state.get("document_job_logs") or {}
    if logs:
        st.caption(f"最近一次后台日志：{logs.get('stdout_path')} / {logs.get('stderr_path')}")

    return document_result


def _render_region_retry_control(document_result: dict, backend: str) -> None:
    retry = st.session_state.get("document_region_retry")
    if not retry or _region_job_is_running():
        return
    region_ids = [str(value) for value in (retry.get("region_ids") or [retry.get("region_id")]) if value]
    available = {
        str(region.get("region_id")): region
        for region in document_result.get("regions", [])
        if region.get("status") != "deleted"
    }
    region_ids = [region_id for region_id in region_ids if region_id in available]
    if not region_ids:
        st.session_state.pop("document_region_retry", None)
        return
    controls = st.columns([0.22, 0.22, 0.56])
    if controls[0].button("重试最近识别任务", key="retry_document_region_job", type="primary"):
        try:
            if len(region_ids) == 1:
                region = available[region_ids[0]]
                _start_region_ocsr_job(
                    document_result,
                    backend,
                    region_ids[0],
                    [int(value) for value in (region.get("bbox") or [0, 0, 1, 1])],
                )
            else:
                _start_confirmed_region_batch_job(
                    document_result,
                    backend,
                    region_ids,
                    scope_label=str(retry.get("scope_label") or "重试批量识别"),
                    page_number=int(retry.get("page_number") or 1),
                )
            st.session_state.pop("document_region_retry", None)
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"识别任务重试失败：{exc}")
    if controls[1].button("不再重试", key="dismiss_document_region_retry"):
        st.session_state.pop("document_region_retry", None)
        st.rerun()
    controls[2].caption(
        f"最近任务包含 {len(region_ids)} 个区域：{str(retry.get('scope_label') or '区域识别')}。"
    )


def _history_key(document_result: dict) -> str:
    return str(document_result.get("document_id") or document_result.get("output_dir") or "document")


def _history_stacks(document_result: dict) -> tuple[list[dict], list[dict]]:
    identity = _history_key(document_result)
    if st.session_state.get("document_history_identity") != identity:
        st.session_state["document_history_identity"] = identity
        st.session_state["document_undo_stack"] = []
        st.session_state["document_redo_stack"] = []
    return (
        st.session_state.setdefault("document_undo_stack", []),
        st.session_state.setdefault("document_redo_stack", []),
    )


def _push_document_history(document_result: dict) -> None:
    undo, redo = _history_stacks(document_result)
    undo.append(deepcopy(document_result))
    if len(undo) > DOCUMENT_HISTORY_LIMIT:
        del undo[:-DOCUMENT_HISTORY_LIMIT]
    redo.clear()


def _restore_document_history(document_result: dict, direction: str) -> dict | None:
    undo, redo = _history_stacks(document_result)
    source, destination = (undo, redo) if direction == "undo" else (redo, undo)
    if not source:
        return None
    restored = source.pop()
    destination.append(deepcopy(document_result))
    if len(destination) > DOCUMENT_HISTORY_LIMIT:
        del destination[:-DOCUMENT_HISTORY_LIMIT]
    result_path = persist_document_result_atomic(restored)
    record_result_payload(restored, result_path)
    return restored


def _restore_document_review_state(document_result: dict, page_numbers: list[int]) -> None:
    saved = document_result.get("review_state") or {}
    if "document_current_page" not in st.session_state:
        page_number = int(saved.get("page_number") or page_numbers[0])
        st.session_state["document_current_page"] = page_number if page_number in page_numbers else page_numbers[0]
    defaults = {
        "document_region_select": str(saved.get("selected_region_id") or ""),
        "document_filter_type": str(saved.get("region_type") or "全部"),
        "document_filter_status": str(saved.get("status") or "全部"),
        "document_show_advanced_regions": bool(saved.get("show_advanced_regions", False)),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _persist_document_review_state(document_result: dict, page_number: int, selected_id: str | None) -> None:
    state = {
        "page_number": int(page_number),
        "selected_region_id": str(selected_id or ""),
        "region_type": str(st.session_state.get("document_filter_type") or "全部"),
        "status": str(st.session_state.get("document_filter_status") or "全部"),
        "show_advanced_regions": bool(st.session_state.get("document_show_advanced_regions", False)),
        "saved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    previous = document_result.get("review_state") or {}
    comparable = {key: previous.get(key) for key in state if key != "saved_at_utc"}
    expected = {key: value for key, value in state.items() if key != "saved_at_utc"}
    if comparable == expected:
        return
    document_result["review_state"] = state
    try:
        persist_document_result_atomic(document_result)
    except OSError:
        pass


def _is_strict_molecule_candidate(region: dict) -> bool:
    if str(region.get("region_type") or "") != "molecule":
        return False
    if str(region.get("source") or "") == "user" or bool(region.get("confirmed")):
        return True
    if str(region.get("status") or "") == "recognized":
        return True
    screening = region.get("screening") or {}
    diagnostics = screening.get("diagnostics") or {}
    structural = screening.get("structural_evidence", diagnostics.get("structural_evidence"))
    return bool(screening.get("passed")) and bool(structural)


def _thumbnail_window(page_numbers: list[int], current_page: int, limit: int = 7) -> list[int]:
    if len(page_numbers) <= limit:
        return page_numbers
    index = page_numbers.index(current_page)
    start = max(0, min(index - limit // 2, len(page_numbers) - limit))
    return page_numbers[start : start + limit]


def _set_document_page(page_number: int) -> None:
    st.session_state["document_requested_page"] = int(page_number)


def _set_document_region(region_id: str) -> None:
    st.session_state["document_requested_region"] = str(region_id)


def _render_page_navigation(pages: list[dict], current_page: int) -> None:
    page_numbers = [int(page.get("page_number", 0)) for page in pages]
    index = page_numbers.index(int(current_page))
    controls = st.columns([0.13, 0.13, 0.48, 0.13, 0.13])
    controls[0].button(
        "⏮ 首页",
        disabled=index == 0,
        key="document_first_page",
        on_click=_set_document_page,
        args=(page_numbers[0],),
    )
    controls[1].button(
        "← 上一页",
        disabled=index == 0,
        key="document_previous_page",
        on_click=_set_document_page,
        args=(page_numbers[max(0, index - 1)],),
    )
    controls[2].caption(f"第 {index + 1} / {len(page_numbers)} 页")
    controls[3].button(
        "下一页 →",
        disabled=index == len(page_numbers) - 1,
        key="document_next_page",
        on_click=_set_document_page,
        args=(page_numbers[min(len(page_numbers) - 1, index + 1)],),
    )
    controls[4].button(
        "末页 ⏭",
        disabled=index == len(page_numbers) - 1,
        key="document_last_page",
        on_click=_set_document_page,
        args=(page_numbers[-1],),
    )

    shown = _thumbnail_window(page_numbers, int(current_page))
    columns = st.columns(len(shown))
    pages_by_number = {int(page.get("page_number", 0)): page for page in pages}
    for column, page_number in zip(columns, shown):
        page = pages_by_number[page_number]
        image_path = Path(str(page.get("image_path") or ""))
        if image_path.is_file():
            column.image(str(image_path), width=95)
        label = f"✓ 第 {page_number} 页" if page_number == int(current_page) else f"第 {page_number} 页"
        column.button(
            label,
            key=f"document_thumbnail_{page_number}",
            disabled=page_number == int(current_page),
            on_click=_set_document_page,
            args=(page_number,),
        )


def _document_workbench(document_result: dict, backend: str, rows: list[dict]) -> dict:
    requested_page = st.session_state.pop("document_requested_page", None)
    if requested_page is not None:
        st.session_state["document_current_page"] = int(requested_page)
    requested_region = st.session_state.pop("document_requested_region", None)
    if requested_region is not None:
        st.session_state["document_region_select"] = str(requested_region)
    restore = st.session_state.pop("document_region_restore", None)
    if restore:
        st.session_state["document_region_select"] = str(restore.get("region_id") or "")
        st.session_state["document_current_page"] = int(restore.get("page_number") or 1)
    document_result = _consume_document_canvas_event(document_result)
    pages = sorted(document_result.get("pages") or [], key=lambda page: int(page.get("page_number", 0)))
    if not pages:
        st.warning("文档结果中没有可显示的页面。")
        return document_result
    active = [region for region in document_result.get("regions", []) if region.get("status") != "deleted"]
    page_numbers = [int(page.get("page_number", 0)) for page in pages]
    _restore_document_review_state(document_result, page_numbers)
    current_page = st.session_state.get("document_current_page")
    if current_page not in page_numbers:
        selected_region_id = str(st.session_state.get("document_region_select") or "")
        selected_region = next((region for region in active if str(region.get("region_id")) == selected_region_id), None)
        st.session_state["document_current_page"] = int((selected_region or {}).get("page_number") or page_numbers[0])
    selected_page_state = int(st.session_state.get("document_current_page") or page_numbers[0])
    page_number = st.selectbox(
        f"论文页码（全文共 {len(page_numbers)} 页）",
        page_numbers,
        index=page_numbers.index(selected_page_state),
        key=f"document_page_picker_{selected_page_state}",
    )
    if int(page_number) != selected_page_state:
        st.session_state["document_requested_page"] = int(page_number)
        st.rerun()
    page = dict(next(item for item in pages if int(item.get("page_number", 0)) == int(page_number)))
    page["review_state"] = document_result.get("review_state") or {}
    _render_page_navigation(pages, int(page_number))
    st.caption(f"正在审核第 {page_numbers.index(int(page_number)) + 1} / {len(page_numbers)} 页；所有页面均已保留在当前任务中。")
    page_regions = [region for region in active if int(region.get("page_number", 0)) == int(page_number)]

    with st.expander(
        "高级筛选：文本、表格、图表标签和宽松候选",
        expanded=bool(st.session_state.get("document_show_advanced_regions", False)),
    ):
        show_advanced = st.checkbox(
            "显示非严格候选及非分子区域",
            value=False,
            key="document_show_advanced_regions",
        )
        if show_advanced:
            st.caption(
                "高级筛选会显示文本、表格、反应、图表标签，以及缺少明确骨架证据的宽松候选；"
                "类型和状态筛选会同步作用于区域导航与画布框。"
            )
    visible = page_regions if show_advanced else [region for region in page_regions if _is_strict_molecule_candidate(region)]
    if not visible:
        if page_regions:
            st.info("本页没有严格分子候选框。可在“高级筛选”中查看宽松候选、文本和表格；新增区域请先点击画布上的“新增框模式”。")
        else:
            st.info("本页未检测到区域；可在画布上先点击“新增框模式”，再拖画新的分子候选框。")
        _render_region_ocsr_job_status(None)
        _render_document_toolbar(document_result, backend, rows, None)
        _render_bbox_dragger(page, [], "", [0, 0, 1, 1])
        _persist_document_review_state(document_result, int(page_number), None)
        return document_result

    selected, filtered = _render_region_navigator(visible, int(page_number))
    document_result = _render_document_toolbar(document_result, backend, rows, selected)
    if selected is not None:
        _sync_region_bbox_state(selected)

    canvas, inspector = st.columns([0.72, 0.28], gap="large")
    if selected is None:
        with canvas:
            _render_bbox_dragger(page, filtered, "", [0, 0, 1, 1])
        with inspector:
            st.subheader("当前区域")
            st.info("当前筛选条件下没有可查看的区域。")
        _persist_document_review_state(document_result, int(page_number), None)
        return document_result
    with canvas:
        preview_bbox = [
            int(st.session_state.get(f"edit_x1_{selected['region_id']}", (selected.get("bbox") or [0, 0, 1, 1])[0])),
            int(st.session_state.get(f"edit_y1_{selected['region_id']}", (selected.get("bbox") or [0, 0, 1, 1])[1])),
            int(st.session_state.get(f"edit_x2_{selected['region_id']}", (selected.get("bbox") or [0, 0, 1, 1])[2])),
            int(st.session_state.get(f"edit_y2_{selected['region_id']}", (selected.get("bbox") or [0, 0, 1, 1])[3])),
        ]
        _render_bbox_dragger(page, filtered, selected["region_id"], preview_bbox)
    with inspector:
        _render_region_inspector(document_result, backend, selected, page, preview_bbox)
    _persist_document_review_state(document_result, int(page_number), str(selected.get("region_id") or ""))
    return document_result


def _document_needs_screening_refresh(document_result: dict) -> bool:
    refresh = ((document_result.get("processing") or {}).get("screening_refresh") or {}).get("config_version")
    if str(refresh or "").endswith(CURRENT_DOCUMENT_SCREENING_SUFFIX):
        return False
    for region in document_result.get("regions") or []:
        if region.get("status") == "deleted" or bool(region.get("confirmed")):
            continue
        if str(region.get("source") or "detector") == "user" or str(region.get("status") or "") == "recognized":
            continue
        if not str((region.get("screening") or {}).get("config_version") or "").endswith(
            CURRENT_DOCUMENT_SCREENING_SUFFIX
        ):
            return True
    return False


def _render_create_only_canvas(document_result: dict) -> None:
    pages = document_result.get("pages") or []
    if not pages:
        return
    page_numbers = [int(page.get("page_number", 0)) for page in pages]
    page_number = st.selectbox("创建区域的页码", page_numbers, key="document_empty_canvas_page")
    page = next(page for page in pages if int(page.get("page_number", 0)) == page_number)
    _render_bbox_dragger(page, [], "", [0, 0, 1, 1])


def _selected_region_id(active: list[dict]) -> str:
    active_ids = [str(region["region_id"]) for region in active]
    current = str(st.session_state.get("document_region_select") or active_ids[0])
    return current if current in active_ids else active_ids[0]


def _filter_document_regions(active: list[dict], selected_type: str, selected_status: str) -> list[dict]:
    return [
        region
        for region in active
        if (selected_type == "全部" or _audit_region_type(region.get("region_type")) == selected_type)
        and _region_matches_status(region, selected_status)
    ]


def _compact_region_option_label(region: dict) -> str:
    status = "已确认" if region.get("confirmed") else status_label(region.get("status") or "detected")
    return (
        f"{region.get('region_id')} · "
        f"{region_type_label(region.get('region_type'))} · {status} · {_screening_reason_label(region)}"
    )


def _render_region_navigator(active: list[dict], page_number: int) -> tuple[dict | None, list[dict]]:
    st.markdown("#### 区域导航")
    present_types = [
        value
        for value in AUDIT_REGION_TYPES
        if any(_audit_region_type(region.get("region_type")) == value for region in active)
    ]
    type_options = ["全部", *present_types]
    if st.session_state.get("document_filter_type") not in type_options:
        st.session_state["document_filter_type"] = "全部"

    status_options = ["全部", "待确认", "已确认", "识别成功", "识别失败", "已跳过"]
    filters = st.columns([0.20, 0.18, 0.46, 0.08, 0.08])
    selected_type = filters[0].selectbox(
        "类型",
        type_options,
        key="document_filter_type",
        format_func=lambda value: "全部" if value == "全部" else REGION_TYPE_LABELS.get(value, value),
    )
    selected_status = filters[1].selectbox("状态", status_options, key="document_filter_status")
    filtered = _filter_document_regions(active, selected_type, selected_status)
    if not filtered:
        filters[2].selectbox("当前区域", ["没有匹配区域"], disabled=True, key=f"document_empty_region_{page_number}")
        filters[3].button("←", disabled=True, key=f"document_previous_region_{page_number}", help="上一个区域")
        filters[4].button("→", disabled=True, key=f"document_next_region_{page_number}", help="下一个区域")
        st.info("当前类型和状态下没有匹配区域；画布也已同步隐藏其他区域框。")
        return None, []

    ids = [str(region["region_id"]) for region in filtered]
    selected_id = _selected_region_id(filtered)
    if str(st.session_state.get("document_region_select") or "") not in ids:
        st.session_state["document_region_select"] = selected_id
    region_by_id = {str(region["region_id"]): region for region in filtered}
    choice = filters[2].selectbox(
        f"当前区域（{len(filtered)} 个）",
        ids,
        index=ids.index(selected_id),
        key=f"document_region_picker_{page_number}_{selected_id}",
        format_func=lambda region_id: _compact_region_option_label(region_by_id[str(region_id)]),
    )
    choice = str(choice)
    if choice != str(st.session_state.get("document_region_select") or ""):
        st.session_state["document_region_select"] = choice
    selected_index = ids.index(choice)
    filters[3].button(
        "←",
        disabled=selected_index == 0,
        key=f"document_previous_region_{page_number}_{choice}",
        help="上一个匹配区域",
        on_click=_set_document_region,
        args=(ids[max(0, selected_index - 1)],),
    )
    filters[4].button(
        "→",
        disabled=selected_index == len(ids) - 1,
        key=f"document_next_region_{page_number}_{choice}",
        help="下一个匹配区域",
        on_click=_set_document_region,
        args=(ids[min(len(ids) - 1, selected_index + 1)],),
    )
    selected = region_by_id[choice]
    st.caption(
        f"第 {selected.get('page_number')} 页 · {choice} · "
        f"线段 {_screening_value(selected, 'long_line_count', 0)} · "
        f"组件 {_screening_value(selected, 'valid_component_count', _screening_value(selected, 'significant_component_count', 0))} · "
        f"{_screening_reason_label(selected)}"
    )
    return selected, filtered


def _render_region_list(active: list[dict], selected_id: str) -> dict | None:
    st.subheader("区域列表")
    present_types = [value for value in AUDIT_REGION_TYPES if any(_audit_region_type(region.get("region_type")) == value for region in active)]
    type_options = ["全部", *present_types]
    if st.session_state.get("document_filter_type") not in type_options:
        st.session_state["document_filter_type"] = "全部"
    selected_type = st.selectbox(
        "类型",
        type_options,
        key="document_filter_type",
        format_func=lambda value: "全部" if value == "全部" else REGION_TYPE_LABELS.get(value, value),
    )
    status_options = ["全部", "待确认", "已确认", "识别成功", "识别失败", "已跳过"]
    selected_status = st.selectbox("状态", status_options, key="document_filter_status")
    filtered = _filter_document_regions(active, selected_type, selected_status)
    if not filtered:
        st.info("没有匹配区域。")
        return None
    ids = [str(region["region_id"]) for region in filtered]
    if selected_id not in ids:
        selected_id = ids[0]
    if str(st.session_state.get("document_region_select") or "") not in ids:
        st.session_state["document_region_select"] = selected_id
    choice = st.radio(
        "当前区域",
        ids,
        index=ids.index(selected_id),
        key="document_region_select",
        format_func=lambda region_id: _region_option_label(next(region for region in filtered if str(region["region_id"]) == region_id)),
        label_visibility="collapsed",
    )
    return next(region for region in filtered if str(region["region_id"]) == str(choice))


def _region_matches_status(region: dict, selected_status: str) -> bool:
    if selected_status == "全部":
        return True
    if selected_status == "待确认":
        return not bool(region.get("confirmed"))
    if selected_status == "已确认":
        return bool(region.get("confirmed"))
    mapping = {
        "识别成功": "recognized",
        "识别失败": "failed",
        "已跳过": "skipped",
    }
    return str(region.get("status") or "") == mapping.get(selected_status)


def _region_option_label(region: dict) -> str:
    status = "已确认" if region.get("confirmed") else status_label(region.get("status") or "detected")
    lines = _screening_value(region, "long_line_count", 0)
    components = _screening_value(
        region,
        "valid_component_count",
        _screening_value(region, "significant_component_count", 0),
    )
    reason = _screening_reason_label(region)
    return (
        f"第 {region.get('page_number')} 页 · "
        f"{region.get('region_id')} · "
        f"{region_type_label(region.get('region_type'))} · {status} · "
        f"线段 {lines} · 组件 {components} · {reason}"
    )


SCREENING_REASON_LABELS = {
    "short_text_hard_reject": "短文本硬拒绝",
    "pdf_text_token": "PDF 文字层短标签",
    "short_sparse_label": "稀疏短标签",
    "pdf_text_layer_overlap": "与 PDF 文字层重叠",
    "figure_label_without_skeleton": "图表内无骨架标签",
    "missing_skeleton_evidence": "缺少分子骨架证据",
    "text_like": "文字形态",
    "possible_molecule": "具备分子骨架证据",
    "multiple_or_merged_region": "多个结构或合并区域",
    "reaction_arrow": "疑似反应箭头",
    "table_like": "表格线框",
    "dense_figure": "高密度普通图像",
    "blank": "空白区域",
    "too_small": "区域过小",
    "uncertain": "需要人工判断",
}


def _screening_value(region: dict, key: str, default=None):
    screening = region.get("screening") or {}
    diagnostics = screening.get("diagnostics") or {}
    value = screening.get(key, diagnostics.get(key, default))
    return default if value is None else value


def _screening_reason_codes(region: dict) -> list[str]:
    screening = region.get("screening") or {}
    codes = screening.get("reason_codes") or []
    if isinstance(codes, str):
        codes = [value.strip() for value in codes.split(",") if value.strip()]
    if not codes:
        message = str(region.get("message") or "")
        codes = [value.strip() for value in message.split(",") if value.strip()]
    return [str(value) for value in codes]


def _screening_reason_label(region: dict) -> str:
    reason = str((region.get("screening") or {}).get("reason") or "").strip()
    if reason:
        return reason[:36]
    codes = _screening_reason_codes(region)
    labels = [SCREENING_REASON_LABELS.get(code, code) for code in codes]
    return "、".join(labels[:2]) or "未记录筛选原因"


def _short_text_false_positive(region: dict) -> bool:
    codes = set(_screening_reason_codes(region))
    short_text_codes = {
        "short_text_hard_reject",
        "pdf_text_token",
        "short_sparse_label",
        "pdf_text_layer_overlap",
        "figure_label_without_skeleton",
    }
    return str(region.get("region_type") or "") in {"text", "figure_label"} and bool(codes & short_text_codes)


def _crop_quality_warnings(page: dict, bbox: list[int], region: dict | None = None) -> list[str]:
    page_width = max(1, int(page.get("width") or 1))
    page_height = max(1, int(page.get("height") or 1))
    x1, y1, x2, y2 = [int(value) for value in bbox]
    width = max(0, x2 - x1)
    height = max(0, y2 - y1)
    warnings: list[str] = []
    if width < 48 or height < 48:
        warnings.append("裁剪尺寸过小，文字、键型或立体标记可能无法可靠识别。")
    if width * height / float(page_width * page_height) < 0.0008:
        warnings.append("裁剪占页面比例过低，建议适当扩大框选并保留结构四周留白。")
    aspect = max(width / max(height, 1), height / max(width, 1))
    if aspect > 12:
        warnings.append("裁剪长宽比异常，可能只框到了文字、坐标轴或一段化学键。")
    reason_codes = set(_screening_reason_codes(region or {}))
    if reason_codes & {"blank", "too_small", "text_like", "missing_skeleton_evidence"}:
        warnings.append("检测诊断显示当前裁剪缺少稳定的分子骨架证据。")
    return warnings


def _render_region_inspector(
    document_result: dict,
    backend: str,
    selected: dict,
    page: dict,
    preview_bbox: list[int],
) -> None:
    selected_id = str(selected["region_id"])
    st.subheader("当前区域")
    st.caption(f"页码：{selected.get('page_number')}；ID：{selected_id}")
    _render_region_crop_preview(page, preview_bbox, width=300)
    quality_warnings = _crop_quality_warnings(page, preview_bbox, selected)
    if quality_warnings:
        st.warning("低质量裁剪警告：\n\n" + "\n\n".join(f"- {message}" for message in quality_warnings))
    st.write(f"**类型：** {region_type_label(selected.get('region_type'))}")
    st.write(f"**状态：** {status_label(selected.get('status'))}")
    st.write(f"**置信度：** {selected.get('detection_confidence') if selected.get('detection_confidence') is not None else '-'}")
    st.write(f"**筛选原因：** {_screening_reason_label(selected)}")
    st.caption(
        f"结构线段：{_screening_value(selected, 'long_line_count', 0)}；"
        f"有效组件：{_screening_value(selected, 'valid_component_count', _screening_value(selected, 'significant_component_count', 0))}；"
        f"骨架证据：{'有' if _screening_value(selected, 'structural_evidence', False) else '无'}"
    )
    candidate_smiles = _region_candidate_smiles(selected)
    if candidate_smiles:
        st.write("**候选 SMILES：**")
        st.code(candidate_smiles, language=None)
        redrawn = str((((selected.get("report") or {}).get("images") or {}).get("redrawn_molecule")) or "")
        if redrawn and Path(redrawn).is_file():
            st.image(redrawn, caption="候选结构重绘", width=300)
        with st.expander("修正候选 SMILES", expanded=False):
            corrected_smiles = st.text_input(
                "SMILES",
                value=candidate_smiles,
                key=f"document_corrected_smiles_{selected_id}",
            )
            st.caption("保存修正时会执行 RDKit 校验、canonical 化、性质重算和结构重绘；原始预测保留在审计数据中。")
            if st.button("应用 SMILES 修正", key=f"document_apply_smiles_{selected_id}", disabled=_region_job_is_running()):
                _apply_edits_with_notice(
                    document_result,
                    backend,
                    [{"action": "correct_smiles", "region_id": selected_id, "smiles": corrected_smiles}],
                    rerun_ocsr=False,
                    message="SMILES 修正已通过校验并完成结构重绘，请再次人工确认。",
                )
    if str(selected.get("status") or "") == "failed":
        st.error(f"识别失败：{selected.get('message') or '模型未返回可用结构。'}")

    bbox = selected.get("bbox") or [0, 0, 1, 1]
    with st.popover("高级信息（坐标与类型）"):
        first_row = st.columns(2)
        x1 = first_row[0].number_input("x1", min_value=0, value=int(st.session_state.get(f"edit_x1_{selected_id}", bbox[0])), key=f"edit_x1_{selected_id}")
        y1 = first_row[1].number_input("y1", min_value=0, value=int(st.session_state.get(f"edit_y1_{selected_id}", bbox[1])), key=f"edit_y1_{selected_id}")
        second_row = st.columns(2)
        x2 = second_row[0].number_input("x2", min_value=1, value=int(st.session_state.get(f"edit_x2_{selected_id}", bbox[2])), key=f"edit_x2_{selected_id}")
        y2 = second_row[1].number_input("y2", min_value=1, value=int(st.session_state.get(f"edit_y2_{selected_id}", bbox[3])), key=f"edit_y2_{selected_id}")
        allowed = list(AUDIT_REGION_TYPES)
        current = _audit_region_type(selected.get("region_type"))
        region_type = st.selectbox(
            "区域类型",
            allowed,
            index=allowed.index(current),
            format_func=lambda value: REGION_TYPE_LABELS[value],
            key=f"edit_type_{selected_id}",
        )
    job_running = _region_job_is_running()
    st.caption("“仅保存框选”不会运行模型；“识别当前区域”会将类型设为分子并确认后启动后台 OCSR。")
    _render_region_ocsr_job_status(selected_id)
    actions = st.columns(2)
    if actions[0].button("仅保存框选", key=f"save_region_{selected_id}", disabled=job_running):
        try:
            updated = save_region_selection(
                document_result,
                selected_id,
                [x1, y1, x2, y2],
                recognize=False,
                region_type=region_type,
            )
            _push_document_history(document_result)
            result_path = persist_document_result_atomic(updated)
            record_result_payload(updated, result_path)
            st.session_state["document_result"] = updated
            st.session_state["document_region_select"] = selected_id
            st.session_state["document_region_notice"] = {"level": "success", "message": "框选已保存，尚未启动识别。"}
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"框选保存失败：{exc}")
    if actions[1].button("识别当前区域", key=f"recognize_region_{selected_id}", type="primary", disabled=job_running):
        try:
            _start_region_ocsr_job(document_result, backend, selected_id, [x1, y1, x2, y2])
            st.session_state["document_region_select"] = selected_id
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"后台识别启动失败：{exc}")
    review = (selected.get("report") or {}).get("human_review") or {}
    structurally_confirmed = bool(review.get("confirmed"))
    confirmation = st.columns(2)
    if candidate_smiles and not structurally_confirmed:
        if confirmation[0].button("确认候选结构", key=f"confirm_structure_{selected_id}", disabled=job_running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [{"action": "confirm_structure", "region_id": selected_id}],
                rerun_ocsr=False,
                message="候选结构已人工确认，可进入正式结构导出。",
            )
    elif candidate_smiles:
        confirmation[0].success("候选结构已人工确认")
        if confirmation[1].button("撤销结构确认", key=f"revoke_structure_{selected_id}", disabled=job_running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [{"action": "revoke_structure_confirmation", "region_id": selected_id}],
                rerun_ocsr=False,
                message="已撤销候选结构确认。",
            )
    elif not bool(selected.get("confirmed")):
        if confirmation[0].button("确认区域", key=f"confirm_region_{selected_id}", disabled=job_running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [{"action": "confirm", "region_id": selected_id, "region_type": "molecule"}],
                rerun_ocsr=False,
                message="区域已确认，可使用本页或全文批量识别。",
            )
    else:
        confirmation[0].success("区域已确认")
        if confirmation[1].button("撤销区域确认", key=f"unconfirm_region_{selected_id}", disabled=job_running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [{"action": "unconfirm", "region_id": selected_id}],
                rerun_ocsr=False,
                message="已撤销区域确认。",
            )
    minor = st.columns(2)
    if minor[0].button("标记忽略", key=f"mark_region_{selected_id}", disabled=job_running):
        _apply_edits_with_notice(
            document_result,
            backend,
            [{"action": "mark", "region_id": selected_id, "region_type": "ignore", "confirmed": True}],
            rerun_ocsr=False,
            message="区域已标记为忽略。",
        )
    with minor[1].popover("删除"):
        st.warning("删除后该区域会从当前工作台中移除。")
        if st.button("确认删除", key=f"delete_region_confirm_{selected_id}", disabled=job_running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [{"action": "delete", "region_id": selected_id, "note": "用户在界面删除区域。"}],
                rerun_ocsr=False,
                message="区域已删除。",
            )


def _region_candidate_smiles(region: dict) -> str | None:
    report = region.get("report") or {}
    final = report.get("final") or region.get("final_result") or {}
    validation = report.get("validation") or {}
    ocsr = report.get("ocsr") or region.get("ocsr") or {}
    value = (
        region.get("final_smiles")
        or final.get("smiles")
        or final.get("canonical_smiles")
        or validation.get("canonical_smiles")
        or ocsr.get("smiles")
    )
    return str(value) if value else None


def _region_job_is_running() -> bool:
    job = st.session_state.get("document_region_job") or {}
    return bool(job and _poll_region_job(job) is None)


def _copy_region_edit(document_result: dict, region: dict, target_page_number: int) -> dict:
    pages = {int(page.get("page_number", 0)): page for page in document_result.get("pages", [])}
    source_page = pages.get(int(region.get("page_number", 0)))
    target_page = pages.get(int(target_page_number))
    if source_page is None or target_page is None:
        raise ValueError("复制区域的源页面或目标页面不存在。")
    source_width = max(1, int(source_page.get("width") or 1))
    source_height = max(1, int(source_page.get("height") or 1))
    target_width = max(1, int(target_page.get("width") or 1))
    target_height = max(1, int(target_page.get("height") or 1))
    x1, y1, x2, y2 = [int(value) for value in (region.get("bbox") or [0, 0, 1, 1])]
    bbox = [
        round(x1 * target_width / source_width),
        round(y1 * target_height / source_height),
        round(x2 * target_width / source_width),
        round(y2 * target_height / source_height),
    ]
    return {
        "action": "add",
        "page_number": int(target_page_number),
        "bbox": bbox,
        "region_type": str(region.get("region_type") or "molecule"),
        "confirmed": False,
        "note": f"Copied from {region.get('region_id')} on adjacent page.",
    }


def _bbox_iou(left: list[int], right: list[int]) -> float:
    lx1, ly1, lx2, ly2 = [int(value) for value in left]
    rx1, ry1, rx2, ry2 = [int(value) for value in right]
    intersection = max(0, min(lx2, rx2) - max(lx1, rx1)) * max(0, min(ly2, ry2) - max(ly1, ry1))
    union = max(1, (lx2 - lx1) * (ly2 - ly1) + (rx2 - rx1) * (ry2 - ry1) - intersection)
    return intersection / union


def _duplicate_region_groups(regions: list[dict], threshold: float = 0.82) -> list[list[str]]:
    active = [
        region for region in regions
        if region.get("status") != "deleted" and str(region.get("region_type") or "") == "molecule"
    ]
    used: set[str] = set()
    groups: list[list[str]] = []
    for region in active:
        region_id = str(region.get("region_id") or "")
        if not region_id or region_id in used:
            continue
        group = [region_id]
        for candidate in active:
            candidate_id = str(candidate.get("region_id") or "")
            if candidate_id == region_id or candidate_id in used:
                continue
            if int(candidate.get("page_number", 0)) != int(region.get("page_number", 0)):
                continue
            if _bbox_iou(list(region.get("bbox") or [0, 0, 1, 1]), list(candidate.get("bbox") or [0, 0, 1, 1])) >= threshold:
                group.append(candidate_id)
        if len(group) > 1:
            groups.append(group)
            used.update(group)
    return groups


def _render_copy_region_popover(document_result: dict, backend: str, selected: dict | None) -> None:
    if selected is None:
        st.info("请先选择一个区域。")
        return
    page_numbers = sorted(int(page.get("page_number", 0)) for page in document_result.get("pages", []))
    current = int(selected.get("page_number", 0))
    adjacent = [page for page in (current - 1, current + 1) if page in page_numbers]
    if not adjacent:
        st.info("当前页没有相邻页面。")
        return
    target = st.radio("目标页面", adjacent, format_func=lambda page: f"第 {page} 页", key=f"copy_target_{selected.get('region_id')}")
    st.caption("将按页面尺寸比例映射坐标；复制后的区域默认保持未确认。")
    if st.button("复制区域", key=f"copy_region_{selected.get('region_id')}", disabled=_region_job_is_running()):
        _apply_edits_with_notice(
            document_result,
            backend,
            [_copy_region_edit(document_result, selected, int(target))],
            rerun_ocsr=False,
            message=f"区域已复制到第 {target} 页。",
        )


def _render_document_toolbar(document_result: dict, backend: str, rows: list[dict], selected: dict | None) -> dict:
    undo, redo = _history_stacks(document_result)
    toolbar = st.columns([0.09, 0.09, 0.11, 0.11, 0.11, 0.11, 0.14, 0.1, 0.14])
    if toolbar[0].button("↶ 撤销", disabled=_region_job_is_running() or not undo, key="document_undo"):
        restored = _restore_document_history(document_result, "undo")
        if restored is not None:
            st.session_state["document_result"] = restored
            st.session_state["document_region_notice"] = {"level": "success", "message": "已撤销上一步区域操作。"}
            st.rerun()
    if toolbar[1].button("↷ 重做", disabled=_region_job_is_running() or not redo, key="document_redo"):
        restored = _restore_document_history(document_result, "redo")
        if restored is not None:
            st.session_state["document_result"] = restored
            st.session_state["document_region_notice"] = {"level": "success", "message": "已重做区域操作。"}
            st.rerun()
    with toolbar[2].popover("新增区域"):
        st.caption("请先点击画布工具栏中的“＋ 新增框模式”，再在页面上拖动画框；画错可直接取消新增。")
    with toolbar[3].popover("复制"):
        _render_copy_region_popover(document_result, backend, selected)
    with toolbar[4].popover("合并"):
        _render_merge_popover(document_result, backend, selected)
    with toolbar[5].popover("拆分"):
        _render_split_popover(document_result, backend, selected)
    with toolbar[6].popover("批量操作"):
        _render_batch_region_popover(document_result, backend)
    with toolbar[7].popover("导出"):
        _download_panel(document_result)
    with toolbar[8].popover("结果表"):
        important = [
            "page_number",
            "region_id",
            "region_type",
            "detection_confidence",
            "status",
            "confirmed",
            "message",
            "final_smiles",
            "valid",
            "review_queued",
            "inference_time_ms",
            "processing_time_ms",
            "screening_reason",
        ]
        display_rows = [{key: row.get(key) for key in important} for row in rows]
        render_records(
            localize_region_rows(display_rows),
            title_keys=("区域 ID",),
            summary_keys=("页码", "区域类型", "状态", "最终 SMILES", "推理耗时(ms)"),
            max_records=100,
        )
    return document_result


def _render_add_region_popover(document_result: dict, backend: str) -> None:
    page_numbers = [int(page["page_number"]) for page in document_result.get("pages", [])]
    if not page_numbers:
        st.info("没有可添加区域的页面。")
        return
    page_number = st.selectbox("页码", page_numbers, key="add_region_page")
    row1 = st.columns(2)
    add_x1 = row1[0].number_input("新区域 x1", min_value=0, value=0, key="add_x1")
    add_y1 = row1[1].number_input("新区域 y1", min_value=0, value=0, key="add_y1")
    row2 = st.columns(2)
    add_x2 = row2[0].number_input("新区域 x2", min_value=1, value=200, key="add_x2")
    add_y2 = row2[1].number_input("新区域 y2", min_value=1, value=200, key="add_y2")
    add_type = st.selectbox("新区域类型", AUDIT_REGION_TYPES, format_func=lambda value: REGION_TYPE_LABELS[value], key="add_type")
    add_confirmed = st.checkbox("添加后立即确认", value=True, key="add_confirmed")
    if st.button("添加区域", key="add_region"):
        _apply_edits_with_notice(
            document_result,
            backend,
            [{
                "action": "add",
                "page_number": page_number,
                "bbox": [add_x1, add_y1, add_x2, add_y2],
                "region_type": add_type,
                "confirmed": add_confirmed,
            }],
            rerun_ocsr=add_confirmed and add_type == "molecule",
            message="区域已添加。",
        )


def _render_merge_popover(document_result: dict, backend: str, selected: dict | None) -> None:
    active = [region for region in document_result.get("regions", []) if region.get("status") != "deleted"]
    if len(active) < 2:
        st.info("至少需要两个区域才能合并。")
        return
    region_ids = [str(region["region_id"]) for region in active]
    default = [str(selected["region_id"])] if selected else []
    merge_ids = st.multiselect("待合并区域", region_ids, default=default, key="merge_region_ids")
    current = _audit_region_type((selected or {}).get("region_type"))
    merge_type = st.selectbox(
        "合并后类型",
        AUDIT_REGION_TYPES,
        index=AUDIT_REGION_TYPES.index(current),
        format_func=lambda value: REGION_TYPE_LABELS[value],
        key="merge_region_type",
    )
    merge_confirmed = st.checkbox("合并后立即确认", value=False, key="merge_confirmed")
    if st.button("合并区域", key="merge_regions", disabled=len(merge_ids) < 2):
        _apply_edits_with_notice(
            document_result,
            backend,
            [{
                "action": "merge",
                "region_ids": merge_ids,
                "region_type": merge_type,
                "confirmed": merge_confirmed,
            }],
            rerun_ocsr=merge_confirmed and merge_type == "molecule",
            message="区域已合并。",
        )


def _render_split_popover(document_result: dict, backend: str, selected: dict | None) -> None:
    if not selected:
        st.info("请先选择一个区域。")
        return
    selected_id = str(selected["region_id"])
    split_direction_label = st.radio("拆分方向", ["左右拆分", "上下拆分"], horizontal=True, key=f"split_direction_{selected_id}")
    split_ratio = st.slider("拆分位置", 0.1, 0.9, 0.5, 0.05, key=f"split_ratio_{selected_id}")
    current = _audit_region_type(selected.get("region_type"))
    split_confirmed = st.checkbox("拆分后立即确认", value=False, key=f"split_confirmed_{selected_id}")
    if st.button("拆分区域", key=f"split_region_{selected_id}"):
        _apply_edits_with_notice(
            document_result,
            backend,
            [{
                "action": "split",
                "region_id": selected_id,
                "direction": "vertical" if split_direction_label == "左右拆分" else "horizontal",
                "split_at": float(split_ratio),
                "region_type": current,
                "confirmed": split_confirmed,
            }],
            rerun_ocsr=split_confirmed and current == "molecule",
            message="区域已拆分。",
        )


def _render_batch_region_popover(document_result: dict, backend: str) -> None:
    active = [region for region in document_result.get("regions", []) if region.get("status") != "deleted"]
    page_numbers = sorted({int(page.get("page_number", 0)) for page in document_result.get("pages", [])})
    if not page_numbers:
        st.info("没有可批量操作的区域。")
        return
    preferred_page = int(st.session_state.get("document_current_page") or page_numbers[0])
    if preferred_page not in page_numbers:
        preferred_page = page_numbers[0]
    page_number_for_batch = st.selectbox(
        "批量识别页码",
        page_numbers,
        index=page_numbers.index(preferred_page),
        key="recognize_page_number",
    )
    confirmed_molecules = [
        region for region in active
        if region.get("region_type") == "molecule" and bool(region.get("confirmed"))
    ]
    page_ids = [
        str(region.get("region_id"))
        for region in confirmed_molecules
        if int(region.get("page_number", 0)) == int(page_number_for_batch)
    ]
    all_ids = [str(region.get("region_id")) for region in confirmed_molecules]
    st.caption(f"本页已确认分子：{len(page_ids)}；全文已确认分子：{len(all_ids)}。未确认框不会进入 OCSR。")
    running = _region_job_is_running()
    if st.button(
        "识别本页已确认区域",
        key=f"recognize_confirmed_page_{page_number_for_batch}",
        disabled=running or not page_ids,
    ):
        try:
            _start_confirmed_region_batch_job(
                document_result,
                backend,
                page_ids,
                scope_label=f"第 {page_number_for_batch} 页批量识别",
                page_number=page_number_for_batch,
            )
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"本页批量识别启动失败：{exc}")
    if st.button(
        "批量识别全部已确认区域",
        key="recognize_all_confirmed_regions",
        type="primary",
        disabled=running or not all_ids,
    ):
        try:
            _start_confirmed_region_batch_job(
                document_result,
                backend,
                all_ids,
                scope_label="全文批量识别",
                page_number=page_number_for_batch,
            )
            st.rerun()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"全文批量识别启动失败：{exc}")

    st.divider()
    st.markdown("**重复区域合并**")
    duplicate_groups = _duplicate_region_groups(active)
    if duplicate_groups:
        st.warning(
            f"检测到 {len(duplicate_groups)} 组高度重叠的分子框，共涉及 "
            f"{sum(len(group) for group in duplicate_groups)} 个区域。"
        )
        with st.expander("查看重复区域组", expanded=False):
            for index, group in enumerate(duplicate_groups, start=1):
                st.caption(f"第 {index} 组：{'、'.join(group)}")
        if st.button("合并全部重复区域", key="merge_all_duplicate_regions", disabled=running):
            _apply_edits_with_notice(
                document_result,
                backend,
                [
                    {
                        "action": "merge",
                        "region_ids": group,
                        "region_type": "molecule",
                        "confirmed": False,
                        "note": "Merged automatically detected duplicate regions.",
                    }
                    for group in duplicate_groups
                ],
                rerun_ocsr=False,
                message=f"已合并 {len(duplicate_groups)} 组重复区域；合并结果需要重新确认。",
            )
    else:
        st.caption("未检测到 IoU 高于 0.82 的重复分子框。")

    st.divider()
    st.markdown("**批量清理短文本误框**")
    short_text_regions = [region for region in active if _short_text_false_positive(region)]
    short_text_ids = [str(region.get("region_id")) for region in short_text_regions]
    page_short_ids = [
        str(region.get("region_id"))
        for region in short_text_regions
        if int(region.get("page_number", 0)) == int(page_number_for_batch)
    ]
    if not short_text_ids:
        st.caption("当前文档没有被规则标记为短文本误框的区域。")
        return
    cleanup_ids = st.multiselect(
        "待删除误框",
        short_text_ids,
        default=page_short_ids,
        format_func=lambda region_id: _region_option_label(
            next(region for region in short_text_regions if str(region.get("region_id")) == region_id)
        ),
        key="document_short_text_cleanup_ids",
    )
    st.caption("默认选择当前页误框；删除前可逐项取消。此操作不会删除分子候选框。")
    cleanup_confirmed = st.checkbox(
        f"确认删除所选 {len(cleanup_ids)} 个短文本误框",
        value=False,
        key="document_short_text_cleanup_confirmed",
    )
    if st.button(
        "删除所选短文本误框",
        disabled=running or not cleanup_ids or not cleanup_confirmed,
        key="document_delete_short_text_false_positives",
    ):
        _apply_edits_with_notice(
            document_result,
            backend,
            [
                {"action": "delete", "region_id": region_id, "note": "批量删除规则标记的短文本误框。"}
                for region_id in cleanup_ids
            ],
            rerun_ocsr=False,
            message=f"已删除 {len(cleanup_ids)} 个短文本误框。",
        )


def _apply_edits_with_notice(
    document_result: dict,
    backend: str,
    edits: list[dict],
    *,
    rerun_ocsr: bool,
    message: str,
) -> None:
    if _region_job_is_running():
        st.error("区域识别任务运行中，请等待完成后再编辑其他区域。")
        return
    try:
        updated = _apply_document_edits(document_result, backend, edits, rerun_ocsr=rerun_ocsr)
        _push_document_history(document_result)
        st.session_state["document_result"] = updated
        st.success(message)
        st.rerun()
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(str(exc))


def _region_editor(document_result: dict, backend: str) -> dict:
    active = [region for region in document_result.get("regions", []) if region.get("status") != "deleted"]
    if active:
        st.subheader("审核和编辑检测区域")
        region_ids = [region["region_id"] for region in active]
        selected_id = st.selectbox("区域", region_ids, key="document_region_select")
        selected = next(region for region in active if region["region_id"] == selected_id)
        # Legacy editor retained for old callers; the active workbench consumes canvas events above.
        bbox = selected.get("bbox") or [0, 0, 1, 1]
        for key, value in zip(("x1", "y1", "x2", "y2"), bbox):
            st.session_state.setdefault(f"edit_{key}_{selected_id}", int(value))

        page = _page_for_region(document_result, selected)
        if page:
            preview_bbox = [
                int(st.session_state.get(f"edit_x1_{selected_id}", bbox[0])),
                int(st.session_state.get(f"edit_y1_{selected_id}", bbox[1])),
                int(st.session_state.get(f"edit_x2_{selected_id}", bbox[2])),
                int(st.session_state.get(f"edit_y2_{selected_id}", bbox[3])),
            ]
            _render_bbox_dragger(page, active, selected_id, preview_bbox)

        first_row = st.columns(2)
        x1 = first_row[0].number_input("x1", min_value=0, value=int(bbox[0]), key=f"edit_x1_{selected_id}")
        y1 = first_row[1].number_input("y1", min_value=0, value=int(bbox[1]), key=f"edit_y1_{selected_id}")
        second_row = st.columns(2)
        x2 = second_row[0].number_input("x2", min_value=1, value=int(bbox[2]), key=f"edit_x2_{selected_id}")
        y2 = second_row[1].number_input("y2", min_value=1, value=int(bbox[3]), key=f"edit_y2_{selected_id}")
        allowed = list(AUDIT_REGION_TYPES)
        current = _audit_region_type(selected.get("region_type"))
        region_type = st.selectbox(
            "区域类型",
            allowed,
            index=allowed.index(current),
            format_func=lambda value: REGION_TYPE_LABELS[value],
            key=f"edit_type_{selected_id}",
        )
        confirmed = bool(selected.get("confirmed"))
        status_text = "已确认" if confirmed else "待确认"
        st.caption(f"当前审核状态：{status_text}；只有已确认的分子区域会被送入 OCSR。")
        actions = st.columns(4)
        if actions[0].button("仅保存框选", key=f"update_region_{selected_id}"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{
                        "action": "update",
                        "region_id": selected_id,
                        "bbox": [x1, y1, x2, y2],
                        "region_type": region_type,
                        "confirmed": confirmed,
                    }],
                    rerun_ocsr=False,
                )
                st.session_state["document_result"] = document_result
                st.success("区域已保存。")
            except RuntimeError as exc:
                st.error(f"区域更新失败：{exc}")
        if actions[1].button("保存并识别", key=f"confirm_region_{selected_id}", type="primary"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{
                        "action": "update",
                        "region_id": selected_id,
                        "bbox": [x1, y1, x2, y2],
                        "region_type": region_type,
                        "confirmed": True,
                    }],
                    rerun_ocsr=region_type == "molecule",
                )
                st.session_state["document_result"] = document_result
                st.success("区域已确认；分子区域已重新识别。")
            except RuntimeError as exc:
                st.error(f"区域确认失败：{exc}")
        if actions[2].button("删除区域", key=f"delete_region_{selected_id}"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{"action": "delete", "region_id": selected_id, "note": "用户在界面删除区域。"}],
                    rerun_ocsr=False,
                )
                st.session_state["document_result"] = document_result
                st.warning("区域已删除。")
            except RuntimeError as exc:
                st.error(f"区域删除失败：{exc}")
        if actions[3].button("标记忽略", key=f"mark_region_{selected_id}"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{"action": "mark", "region_id": selected_id, "region_type": "ignore", "confirmed": True}],
                    rerun_ocsr=False,
                )
                st.session_state["document_result"] = document_result
                st.success("区域已标记为忽略。")
            except RuntimeError as exc:
                st.error(f"区域标记失败：{exc}")

        page_numbers = sorted({int(region["page_number"]) for region in active})
        page_number_for_batch = st.selectbox("批量确认页码", page_numbers, key="confirm_page_number")
        batch_actions = st.columns(2)
        if batch_actions[0].button("确认本页全部区域", key=f"confirm_page_{page_number_for_batch}"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{"action": "confirm_page", "page_number": page_number_for_batch, "note": "页面批量确认。"}],
                    rerun_ocsr=False,
                )
                st.session_state["document_result"] = document_result
                st.success("本页区域已批量确认。")
            except RuntimeError as exc:
                st.error(f"本页确认失败：{exc}")
        if batch_actions[1].button("确认本页并识别分子", key=f"confirm_page_ocsr_{page_number_for_batch}"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{"action": "confirm_page", "page_number": page_number_for_batch, "note": "页面批量保存后识别。"}],
                    rerun_ocsr=True,
                )
                st.session_state["document_result"] = document_result
                st.success("本页区域已确认，已确认分子区域已识别。")
            except RuntimeError as exc:
                st.error(f"本页识别失败：{exc}")

        with st.expander("合并两个或多个区域", expanded=False):
            merge_ids = st.multiselect("待合并区域", region_ids, default=[selected_id], key="merge_region_ids")
            merge_type = st.selectbox(
                "合并后类型",
                AUDIT_REGION_TYPES,
                index=AUDIT_REGION_TYPES.index(current),
                format_func=lambda value: REGION_TYPE_LABELS[value],
                key="merge_region_type",
            )
            merge_confirmed = st.checkbox("合并后立即确认", value=False, key="merge_confirmed")
            if st.button("合并区域", key="merge_regions", disabled=len(merge_ids) < 2):
                try:
                    document_result = _apply_document_edits(
                        document_result,
                        backend,
                        [{
                            "action": "merge",
                            "region_ids": merge_ids,
                            "region_type": merge_type,
                            "confirmed": merge_confirmed,
                        }],
                        rerun_ocsr=merge_confirmed and merge_type == "molecule",
                    )
                    st.session_state["document_result"] = document_result
                    st.success("区域已合并。")
                except RuntimeError as exc:
                    st.error(f"区域合并失败：{exc}")

        with st.expander("拆分当前区域", expanded=False):
            split_direction_label = st.radio("拆分方向", ["左右拆分", "上下拆分"], horizontal=True, key=f"split_direction_{selected_id}")
            split_ratio = st.slider("拆分位置", 0.1, 0.9, 0.5, 0.05, key=f"split_ratio_{selected_id}")
            split_confirmed = st.checkbox("拆分后立即确认", value=False, key=f"split_confirmed_{selected_id}")
            if st.button("拆分区域", key=f"split_region_{selected_id}"):
                try:
                    document_result = _apply_document_edits(
                        document_result,
                        backend,
                        [{
                            "action": "split",
                            "region_id": selected_id,
                            "direction": "vertical" if split_direction_label == "左右拆分" else "horizontal",
                            "split_at": float(split_ratio),
                            "region_type": region_type,
                            "confirmed": split_confirmed,
                        }],
                        rerun_ocsr=split_confirmed and region_type == "molecule",
                    )
                    st.session_state["document_result"] = document_result
                    st.success("区域已拆分。")
                except RuntimeError as exc:
                    st.error(f"区域拆分失败：{exc}")

    st.subheader("添加遗漏区域")
    page_numbers = [int(page["page_number"]) for page in document_result.get("pages", [])]
    if page_numbers:
        page_number = st.selectbox("页码", page_numbers, key="add_region_page")
        row1 = st.columns(2)
        add_x1 = row1[0].number_input("新区域 x1", min_value=0, value=0, key="add_x1")
        add_y1 = row1[1].number_input("新区域 y1", min_value=0, value=0, key="add_y1")
        row2 = st.columns(2)
        add_x2 = row2[0].number_input("新区域 x2", min_value=1, value=200, key="add_x2")
        add_y2 = row2[1].number_input("新区域 y2", min_value=1, value=200, key="add_y2")
        add_type = st.selectbox(
            "新区域类型",
            AUDIT_REGION_TYPES,
            format_func=lambda value: REGION_TYPE_LABELS[value],
            key="add_type",
        )
        add_confirmed = st.checkbox("添加后立即确认", value=True, key="add_confirmed")
        if st.button("添加区域", key="add_region"):
            try:
                document_result = _apply_document_edits(
                    document_result,
                    backend,
                    [{
                        "action": "add",
                        "page_number": page_number,
                        "bbox": [add_x1, add_y1, add_x2, add_y2],
                        "region_type": add_type,
                        "confirmed": add_confirmed,
                    }],
                    rerun_ocsr=add_confirmed and add_type == "molecule",
                )
                st.session_state["document_result"] = document_result
                st.success("区域已添加。")
            except RuntimeError as exc:
                st.error(f"区域添加失败：{exc}")
    return document_result


def _audit_region_type(region_type: str | None) -> str:
    value = str(region_type or "ignore")
    if value in AUDIT_REGION_TYPES:
        return value
    if value in {"reaction_arrow", "reaction_condition", "reaction_like"}:
        return "reaction"
    if value == "figure_label":
        return "text"
    if value in {"text", "table", "molecule"}:
        return value
    return "ignore"


def _page_for_region(document_result: dict, region: dict) -> dict | None:
    page_number = int(region.get("page_number", 0))
    return next((page for page in document_result.get("pages", []) if int(page.get("page_number", 0)) == page_number), None)


def _sync_region_bbox_state(region: dict) -> None:
    region_id = str(region.get("region_id") or "")
    bbox = [int(value) for value in (region.get("bbox") or [0, 0, 1, 1])]
    identity = tuple(bbox)
    if st.session_state.get(f"edit_bbox_identity_{region_id}") == identity:
        return
    st.session_state[f"edit_bbox_identity_{region_id}"] = identity
    for coord, value in zip(("x1", "y1", "x2", "y2"), bbox):
        st.session_state[f"edit_{coord}_{region_id}"] = value


def _consume_document_canvas_event(document_result: dict) -> dict:
    params = st.query_params
    if not params.get("document_region_editor_key"):
        return document_result
    try:
        nonce = str(params.get("doc_bbox_nonce") or "")
        if not nonce or st.session_state.get("document_canvas_event_nonce") == nonce:
            return document_result
        page_number = int(float(str(params.get("doc_bbox_page") or "0")))
        page = next(
            (item for item in document_result.get("pages", []) if int(item.get("page_number", 0)) == page_number),
            None,
        )
        if page is None:
            raise ValueError(f"找不到画布对应的第 {page_number} 页。")
        event = canvas_event_from_query(params, page)
        if event is None:
            return document_result
        updated, selected_id = apply_canvas_event(document_result, event)
        if updated is not document_result:
            _push_document_history(document_result)
            result_path = persist_document_result_atomic(updated)
            record_result_payload(updated, result_path)
            st.session_state["document_result"] = updated
            action_label = {"create": "新框已创建", "update": "框选调整已保存", "delete": "区域已删除"}.get(event["action"], "区域已更新")
            st.success(f"{action_label}（原始页面坐标）。")
        if selected_id:
            st.session_state["document_region_select"] = str(selected_id)
        elif event.get("action") == "delete":
            remaining = [
                region for region in updated.get("regions", [])
                if region.get("status") != "deleted" and int(region.get("page_number", 0)) == page_number
            ]
            if remaining:
                st.session_state["document_region_select"] = str(remaining[0].get("region_id"))
        st.session_state["document_current_page"] = int(page_number)
        st.session_state["document_canvas_event_nonce"] = nonce
        return updated
    except (TypeError, ValueError, OSError) as exc:
        st.error(f"区域框选保存失败：{exc}")
        return document_result
    finally:
        for key in DOCUMENT_BBOX_QUERY_KEYS:
            try:
                if key in params:
                    del params[key]
            except KeyError:
                continue


def _render_bbox_dragger(page: dict, regions: list[dict], selected_id: str, bbox: list[int]) -> None:
    image_path = Path(str(page.get("image_path") or ""))
    if not image_path.is_file():
        st.warning(f"页面图片不存在：{image_path}")
        return
    width = int(page.get("width") or 1)
    height = int(page.get("height") or 1)
    display_width = min(860, width)
    display_height = max(160, int(display_width * height / max(width, 1)))
    viewport_height = min(680, display_height)
    mime = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    overlay_regions = [
        {
            "region_id": region.get("region_id"),
            "bbox": region.get("bbox"),
            "region_type": region.get("region_type"),
            "confirmed": bool(region.get("confirmed")),
        }
        for region in regions
        if int(region.get("page_number", 0)) == int(page.get("page_number", 0))
    ]
    payload = {
        "key": selected_id or f"page-{int(page.get('page_number', 0))}-new",
        "document_id": str(page.get("document_id") or image_path.parent.name),
        "review_saved_at_utc": str((page.get("review_state") or {}).get("saved_at_utc") or ""),
        "selected_region_id": selected_id,
        "has_selection": bool(selected_id),
        "locked": _region_job_is_running(),
        "page_number": int(page.get("page_number", 0)),
        "src": f"data:{mime};base64,{encoded}",
        "width": width,
        "height": height,
        "bbox": [int(value) for value in bbox],
        "regions": overlay_regions,
    }
    html = f"""
    <div style="font: 14px system-ui, sans-serif; color: #163232;">
      <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; margin-bottom:6px;">
        <span id="editor-status" style="padding:5px 9px; border-radius:6px; background:#edf7f6;">点击任意框即可立即选择；新增框请先进入新增模式。</span>
        <span style="display:flex; gap:7px;">
          <button id="zoom-out" type="button" title="缩小" style="border:1px solid #94a3b8; background:white; border-radius:6px; padding:5px 9px;">−</button>
          <button id="zoom-reset" type="button" title="恢复 100%" style="border:1px solid #94a3b8; background:white; border-radius:6px; padding:5px 9px;"><span id="zoom-label">100%</span></button>
          <button id="zoom-in" type="button" title="放大" style="border:1px solid #94a3b8; background:white; border-radius:6px; padding:5px 9px;">＋</button>
          <button id="pan-mode" type="button" style="white-space:nowrap; border:1px solid #64748b; color:#475569; background:white; border-radius:6px; padding:5px 9px;">✋ 拖动画布</button>
          <span id="draft-actions" style="display:none; gap:7px;">
            <button id="save-draft" type="button" style="white-space:nowrap; border:1px solid #0f766e; color:white; background:#0f766e; border-radius:6px; padding:5px 9px;">保存调整</button>
            <button id="cancel-draft" type="button" style="white-space:nowrap; border:1px solid #64748b; color:#475569; background:white; border-radius:6px; padding:5px 9px;">取消调整</button>
          </span>
          <button id="new-region" type="button" style="white-space:nowrap; border:1px solid #0f766e; color:#0f766e; background:white; border-radius:6px; padding:5px 9px;">＋ 新增框模式</button>
          <button id="delete-region" type="button" style="white-space:nowrap; border:1px solid #b42318; color:#b42318; background:white; border-radius:6px; padding:5px 9px;">删除当前框（Delete）</button>
        </span>
      </div>
      <div style="font-size:12px; color:#607575; margin-bottom:6px;">进入新增模式后，在空白处拖动画新框；拖动框内移动；拖动四角缩放；方向键微调（Shift 为 10 像素）；Esc 取消当前拖动或草稿。松手后先预览，点击保存才提交。</div>
      <div id="canvas-viewport" style="width:100%; height:{viewport_height}px; overflow:auto; border:1px solid #c8d7d7; border-radius:6px; background:#eef3f3;">
        <div id="canvas-stage" style="position:relative; display:inline-block; line-height:0;">
          <img id="doc-region-image" src="{payload['src']}" style="width:{display_width}px; max-width:none; display:block; user-select:none; border-radius:5px;" draggable="false" />
          <canvas id="doc-region-overlay" tabindex="0" style="position:absolute; inset:0; outline:none;"></canvas>
        </div>
      </div>
    </div>
    <script>
      const payload = {json.dumps(payload, ensure_ascii=False)};
      const image = document.getElementById("doc-region-image");
      const canvas = document.getElementById("doc-region-overlay");
      const viewport = document.getElementById("canvas-viewport");
      const zoomOutButton = document.getElementById("zoom-out");
      const zoomResetButton = document.getElementById("zoom-reset");
      const zoomInButton = document.getElementById("zoom-in");
      const zoomLabel = document.getElementById("zoom-label");
      const panButton = document.getElementById("pan-mode");
      const deleteButton = document.getElementById("delete-region");
      const newButton = document.getElementById("new-region");
      const draftActions = document.getElementById("draft-actions");
      const saveButton = document.getElementById("save-draft");
      const cancelButton = document.getElementById("cancel-draft");
      const status = document.getElementById("editor-status");
      const ctx = canvas.getContext("2d");
      const localRegions = (payload.regions || []).map((region) => ({{
        ...region,
        bbox: (region.bbox || []).slice(),
      }}));
      let selectedId = payload.selected_region_id || null;
      let bbox = payload.bbox.slice();
      let originalBbox = bbox.slice();
      let drag = null;
      let createMode = false;
      let pendingAction = null;
      let previousSelection = null;
      let submitting = false;
      let zoom = 1;
      let panMode = false;
      let panDrag = null;
      const selectionStorageKey = `document-region-selection:${{payload.document_id}}:${{payload.page_number}}`;

      try {{
        const storedValue = JSON.parse(window.localStorage.getItem(selectionStorageKey) || "null");
        const serverSavedAt = Date.parse(payload.review_saved_at_utc || "") || 0;
        const restoredSelection = storedValue && storedValue.savedAt > serverSavedAt ? storedValue.regionId : null;
        const restoredRegion = localRegions.find((region) => String(region.region_id) === String(restoredSelection));
        if (restoredRegion) {{
          selectedId = String(restoredRegion.region_id);
          bbox = restoredRegion.bbox.slice();
          originalBbox = bbox.slice();
        }}
      }} catch (_error) {{}}

      function applyZoom(nextZoom) {{
        const oldWidth = Math.max(1, image.getBoundingClientRect().width);
        const centerX = viewport.scrollLeft + viewport.clientWidth / 2;
        const centerY = viewport.scrollTop + viewport.clientHeight / 2;
        zoom = Math.max(0.5, Math.min(3, Math.round(nextZoom * 10) / 10));
        image.style.width = `${{Math.round({display_width} * zoom)}}px`;
        zoomLabel.textContent = `${{Math.round(zoom * 100)}}%`;
        requestAnimationFrame(() => {{
          draw();
          const ratio = image.getBoundingClientRect().width / oldWidth;
          viewport.scrollLeft = centerX * ratio - viewport.clientWidth / 2;
          viewport.scrollTop = centerY * ratio - viewport.clientHeight / 2;
        }});
      }}

      function syncControls() {{
        const hasDraft = Boolean(pendingAction);
        draftActions.style.display = hasDraft ? "inline-flex" : "none";
        saveButton.textContent = pendingAction === "create" ? "确认新增" : "保存调整";
        cancelButton.textContent = pendingAction === "create" ? "取消新增" : "取消调整";
        deleteButton.style.display = selectedId && !hasDraft && !payload.locked ? "inline-block" : "none";
        newButton.style.display = payload.locked ? "none" : "inline-block";
        newButton.disabled = hasDraft || submitting;
        deleteButton.disabled = submitting;
        saveButton.disabled = submitting;
        cancelButton.disabled = submitting;
        newButton.style.opacity = newButton.disabled ? "0.55" : "1";
        newButton.textContent = createMode ? "取消新增模式" : "＋ 新增框模式";
        newButton.style.background = createMode ? "#dff3f0" : "white";
        panButton.style.background = panMode ? "#dff3f0" : "white";
        panButton.textContent = panMode ? "✓ 正在拖动画布" : "✋ 拖动画布";
      }}
      function selectRegion(region) {{
        if (!region || !Array.isArray(region.bbox) || region.bbox.length !== 4) return;
        selectedId = String(region.region_id);
        try {{
          window.localStorage.setItem(selectionStorageKey, JSON.stringify({{ regionId: selectedId, savedAt: Date.now() }}));
        }} catch (_error) {{}}
        bbox = region.bbox.slice();
        originalBbox = bbox.slice();
        previousSelection = null;
        createMode = false;
        pendingAction = null;
        status.textContent = `已选择 ${{selectedId}}：可直接移动、缩放、删除或方向键微调。`;
        syncControls();
        draw();
      }}
      function restorePreviousSelection(message) {{
        if (previousSelection) {{
          selectedId = previousSelection.id;
          bbox = previousSelection.bbox.slice();
          originalBbox = bbox.slice();
        }} else {{
          selectedId = null;
          bbox = [0, 0, 1, 1];
          originalBbox = bbox.slice();
        }}
        previousSelection = null;
        createMode = false;
        pendingAction = null;
        drag = null;
        status.textContent = message;
        syncControls();
        draw();
      }}
      function cancelPending() {{
        if (pendingAction === "create" || createMode) {{
          restorePreviousSelection("已取消本次新增，未保存任何新框。");
          return;
        }}
        if (drag) bbox = drag.bbox.slice();
        else bbox = originalBbox.slice();
        drag = null;
        pendingAction = null;
        status.textContent = selectedId ? `已取消调整，仍选择 ${{selectedId}}。` : "已取消调整。";
        syncControls();
        draw();
      }}
      syncControls();
      if (selectedId) status.textContent = `已选择 ${{selectedId}}：可直接移动、缩放、删除或方向键微调。`;

      function scale() {{
        return {{ sx: canvas.width / payload.width, sy: canvas.height / payload.height }};
      }}
      function clampBox(box) {{
        box[0] = Math.max(0, Math.min(payload.width - 1, Math.round(box[0])));
        box[1] = Math.max(0, Math.min(payload.height - 1, Math.round(box[1])));
        box[2] = Math.max(1, Math.min(payload.width, Math.round(box[2])));
        box[3] = Math.max(1, Math.min(payload.height, Math.round(box[3])));
        if (box[2] <= box[0]) box[2] = Math.min(payload.width, box[0] + 1);
        if (box[3] <= box[1]) box[3] = Math.min(payload.height, box[1] + 1);
        return box;
      }}
      function point(event) {{
        const rect = image.getBoundingClientRect();
        return {{
          x: (event.clientX - rect.left) * payload.width / rect.width,
          y: (event.clientY - rect.top) * payload.height / rect.height,
        }};
      }}
      function hitHandle(p) {{
        if (!selectedId || pendingAction === "create") return null;
        const handles = [
          ["nw", bbox[0], bbox[1]], ["ne", bbox[2], bbox[1]],
          ["sw", bbox[0], bbox[3]], ["se", bbox[2], bbox[3]],
        ];
        for (const [name, x, y] of handles) {{
          if (Math.abs(p.x - x) <= 14 && Math.abs(p.y - y) <= 14) return name;
        }}
        if (p.x >= bbox[0] && p.x <= bbox[2] && p.y >= bbox[1] && p.y <= bbox[3]) return "move";
        return null;
      }}
      function hitRegion(p) {{
        const regions = localRegions.filter((region) => {{
          const box = region.bbox || [];
          return box.length === 4 && p.x >= box[0] && p.x <= box[2] && p.y >= box[1] && p.y <= box[3];
        }});
        regions.sort((left, right) => {{
          const a = (left.bbox[2] - left.bbox[0]) * (left.bbox[3] - left.bbox[1]);
          const b = (right.bbox[2] - right.bbox[0]) * (right.bbox[3] - right.bbox[1]);
          return a - b;
        }});
        return regions[0] || null;
      }}
      function draw() {{
        const rect = image.getBoundingClientRect();
        canvas.width = Math.max(1, Math.round(rect.width));
        canvas.height = Math.max(1, Math.round(rect.height));
        canvas.style.width = rect.width + "px";
        canvas.style.height = rect.height + "px";
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const s = scale();
        for (const region of localRegions) {{
          const selected = String(region.region_id) === String(selectedId);
          const box = selected ? bbox : (region.bbox || []);
          if (box.length !== 4) continue;
          ctx.strokeStyle = selected ? "#0f766e" : (region.confirmed ? "#2563eb" : "#8a8f98");
          ctx.lineWidth = selected ? 3 : 1.5;
          ctx.setLineDash(selected ? [] : [5, 4]);
          ctx.strokeRect(box[0] * s.sx, box[1] * s.sy, (box[2] - box[0]) * s.sx, (box[3] - box[1]) * s.sy);
          ctx.setLineDash([]);
        }}
        if (pendingAction === "create" || (drag && drag.mode === "create")) {{
          ctx.fillStyle = "rgba(15, 118, 110, 0.13)";
          ctx.strokeStyle = "#0f766e";
          ctx.lineWidth = 3;
          ctx.fillRect(bbox[0] * s.sx, bbox[1] * s.sy, (bbox[2] - bbox[0]) * s.sx, (bbox[3] - bbox[1]) * s.sy);
          ctx.strokeRect(bbox[0] * s.sx, bbox[1] * s.sy, (bbox[2] - bbox[0]) * s.sx, (bbox[3] - bbox[1]) * s.sy);
        }}
        if (selectedId && pendingAction !== "create") {{
          for (const [x, y] of [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[0], bbox[3]], [bbox[2], bbox[3]]]) {{
            ctx.fillStyle = "#0f766e";
            ctx.fillRect(x * s.sx - 5, y * s.sy - 5, 10, 10);
            ctx.strokeStyle = "white";
            ctx.lineWidth = 2;
            ctx.strokeRect(x * s.sx - 5, y * s.sy - 5, 10, 10);
          }}
        }}
      }}
      function submitEvent(action, regionId, box) {{
        if (submitting) return;
        submitting = true;
        syncControls();
        try {{
          if (action === "create" || action === "delete") window.localStorage.removeItem(selectionStorageKey);
          else if (regionId) window.localStorage.setItem(
            selectionStorageKey,
            JSON.stringify({{ regionId: String(regionId), savedAt: Date.now() }}),
          );
        }} catch (_error) {{}}
        const params = new URLSearchParams(window.top.location.search);
        params.set("document_region_editor_key", payload.key);
        params.set("doc_bbox_action", action);
        params.set("doc_bbox_region_id", regionId || "");
        params.set("doc_bbox_page", String(payload.page_number));
        if (box) {{
          const s = scale();
          params.set("doc_bbox_x1", String(box[0] * s.sx));
          params.set("doc_bbox_y1", String(box[1] * s.sy));
          params.set("doc_bbox_x2", String(box[2] * s.sx));
          params.set("doc_bbox_y2", String(box[3] * s.sy));
          params.set("doc_canvas_width", String(canvas.width));
          params.set("doc_canvas_height", String(canvas.height));
        }}
        params.set("doc_bbox_nonce", String(Date.now()));
        window.top.location.href = window.top.location.pathname + "?" + params.toString();
      }}
      canvas.addEventListener("mousedown", (event) => {{
        if (submitting) return;
        if (panMode) {{
          panDrag = {{
            x: event.clientX,
            y: event.clientY,
            left: viewport.scrollLeft,
            top: viewport.scrollTop,
          }};
          canvas.style.cursor = "grabbing";
          event.preventDefault();
          return;
        }}
        if (payload.locked) return;
        if (pendingAction) {{
          status.textContent = "当前调整尚未处理，请先保存或取消。";
          return;
        }}
        const p = point(event);
        if (createMode) {{
          bbox = [p.x, p.y, p.x + 1, p.y + 1];
          drag = {{ mode: "create", start: p, bbox: bbox.slice(), changed: false }};
          status.textContent = "正在新增框：拖到合适大小后松开鼠标。";
          draw();
        }} else {{
          const region = hitRegion(p);
          if (region && String(region.region_id) !== String(selectedId)) {{
            selectRegion(region);
            canvas.focus();
            event.preventDefault();
            return;
          }}
          const mode = hitHandle(p);
          if (mode) {{
            originalBbox = bbox.slice();
            drag = {{ mode, start: p, bbox: bbox.slice(), changed: false }};
            status.textContent = mode === "move" ? "正在移动框选；松手后可预览并决定是否保存。" : "正在缩放框选；松手后可预览并决定是否保存。";
          }} else if (region) {{
            selectRegion(region);
          }} else {{
            status.textContent = "空白处不会直接新增；请先点击“＋ 新增框模式”。";
          }}
        }}
        canvas.focus();
        event.preventDefault();
      }});
      canvas.addEventListener("mousemove", (event) => {{
        if (panDrag) {{
          viewport.scrollLeft = panDrag.left - (event.clientX - panDrag.x);
          viewport.scrollTop = panDrag.top - (event.clientY - panDrag.y);
          return;
        }}
        const p = point(event);
        if (!drag) {{
          if (panMode) {{ canvas.style.cursor = "grab"; return; }}
          const hover = createMode ? "create" : hitHandle(p);
          canvas.style.cursor = hover === "move" ? "move" : (
            hover === "nw" || hover === "se" ? "nwse-resize" : (
              hover === "ne" || hover === "sw" ? "nesw-resize" : "crosshair"
            )
          );
          return;
        }}
        const dx = p.x - drag.start.x;
        const dy = p.y - drag.start.y;
        if (Math.abs(dx) > 1 || Math.abs(dy) > 1) drag.changed = true;
        const next = drag.bbox.slice();
        if (drag.mode === "create") {{
          next[0] = Math.min(drag.start.x, p.x);
          next[1] = Math.min(drag.start.y, p.y);
          next[2] = Math.max(drag.start.x, p.x);
          next[3] = Math.max(drag.start.y, p.y);
        }} else if (drag.mode === "move") {{
          next[0] += dx; next[2] += dx; next[1] += dy; next[3] += dy;
          const w = next[2] - next[0], h = next[3] - next[1];
          if (next[0] < 0) {{ next[0] = 0; next[2] = w; }}
          if (next[1] < 0) {{ next[1] = 0; next[3] = h; }}
          if (next[2] > payload.width) {{ next[2] = payload.width; next[0] = payload.width - w; }}
          if (next[3] > payload.height) {{ next[3] = payload.height; next[1] = payload.height - h; }}
        }} else {{
          if (drag.mode.includes("w")) next[0] += dx;
          if (drag.mode.includes("e")) next[2] += dx;
          if (drag.mode.includes("n")) next[1] += dy;
          if (drag.mode.includes("s")) next[3] += dy;
        }}
        bbox = clampBox(next);
        draw();
      }});
      window.addEventListener("mouseup", () => {{
        if (panDrag) {{
          panDrag = null;
          canvas.style.cursor = panMode ? "grab" : "crosshair";
          return;
        }}
        if (!drag) return;
        const finished = drag;
        drag = null;
        if (finished.mode === "create") {{
          if (!finished.changed || (bbox[2] - bbox[0]) < 3 || (bbox[3] - bbox[1]) < 3) {{
            bbox = [0, 0, 1, 1];
            status.textContent = "框选范围太小，请继续在页面上拖动；也可点击“取消新增模式”。";
            draw();
            return;
          }}
          createMode = false;
          pendingAction = "create";
          status.textContent = "新框仅为本地草稿：确认无误后点击“确认新增”，或点击“取消新增”。";
        }} else if (finished.changed) {{
          pendingAction = "update";
          status.textContent = "调整仅为本地预览：点击“保存调整”提交，或点击“取消调整”恢复。";
        }} else {{
          bbox = finished.bbox.slice();
          status.textContent = `已选择 ${{selectedId}}，位置未改变。`;
        }}
        syncControls();
        draw();
      }});
      newButton.addEventListener("click", () => {{
        if (submitting || pendingAction) return;
        if (createMode) {{
          restorePreviousSelection("已取消新增模式，未保存任何新框。");
          return;
        }}
        previousSelection = selectedId ? {{ id: selectedId, bbox: bbox.slice() }} : null;
        selectedId = null;
        createMode = true;
        bbox = [0, 0, 1, 1];
        status.textContent = "新增框模式：请在页面上按住鼠标并拖动；Esc 可随时取消。";
        syncControls();
        draw();
        canvas.focus();
      }});
      zoomOutButton.addEventListener("click", () => applyZoom(zoom - 0.2));
      zoomResetButton.addEventListener("click", () => applyZoom(1));
      zoomInButton.addEventListener("click", () => applyZoom(zoom + 0.2));
      panButton.addEventListener("click", () => {{
        if (pendingAction || createMode || drag) {{
          status.textContent = "请先保存或取消当前框选草稿，再切换拖动画布。";
          return;
        }}
        panMode = !panMode;
        syncControls();
        canvas.style.cursor = panMode ? "grab" : "crosshair";
        status.textContent = panMode ? "拖动画布已开启：按住页面并拖动查看放大后的区域。" : "已退出拖动画布，可继续选择或编辑区域。";
        canvas.focus();
      }});
      saveButton.addEventListener("click", () => {{
        if (!pendingAction || submitting) return;
        const action = pendingAction;
        status.textContent = action === "create" ? "正在保存新框…" : "正在保存调整…";
        submitEvent(action, action === "update" ? selectedId : null, bbox);
      }});
      cancelButton.addEventListener("click", cancelPending);
      deleteButton.addEventListener("click", () => {{
        if (selectedId && !pendingAction && window.confirm(`确认删除区域 ${{selectedId}}？`)) {{
          status.textContent = "正在删除当前框…";
          submitEvent("delete", selectedId, null);
        }}
      }});
      canvas.addEventListener("keydown", (event) => {{
        if (event.key === "Escape" && (drag || pendingAction || createMode)) {{
          cancelPending();
          event.preventDefault();
          return;
        }}
        if (selectedId && !pendingAction && (event.key === "Delete" || event.key === "Backspace") && window.confirm(`确认删除区域 ${{selectedId}}？`)) {{
          event.preventDefault();
          status.textContent = "正在删除当前框…";
          submitEvent("delete", selectedId, null);
          return;
        }}
        if (selectedId && !createMode && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {{
          if (!pendingAction) originalBbox = bbox.slice();
          const step = event.shiftKey ? 10 : 1;
          const dx = event.key === "ArrowLeft" ? -step : (event.key === "ArrowRight" ? step : 0);
          const dy = event.key === "ArrowUp" ? -step : (event.key === "ArrowDown" ? step : 0);
          const moved = [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy];
          const width = bbox[2] - bbox[0], height = bbox[3] - bbox[1];
          if (moved[0] < 0) {{ moved[0] = 0; moved[2] = width; }}
          if (moved[1] < 0) {{ moved[1] = 0; moved[3] = height; }}
          if (moved[2] > payload.width) {{ moved[2] = payload.width; moved[0] = payload.width - width; }}
          if (moved[3] > payload.height) {{ moved[3] = payload.height; moved[1] = payload.height - height; }}
          bbox = clampBox(moved);
          pendingAction = "update";
          draw();
          syncControls();
          status.textContent = "已在本地微调；点击“保存调整”提交，或点击“取消调整”恢复。";
          event.preventDefault();
        }}
      }});
      image.addEventListener("load", draw);
      window.addEventListener("resize", draw);
      draw();
    </script>
    """
    components.html(html, height=viewport_height + 148)


def _render_region_crop_preview(page: dict, bbox: list[int], *, width: int = 420) -> None:
    image_path = Path(str(page.get("image_path") or ""))
    if not image_path.is_file():
        return
    image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        st.warning("无法生成当前框选的裁剪预览。")
        return
    image_height, image_width = image.shape[:2]
    x1, y1, x2, y2 = [int(value) for value in bbox]
    x1, x2 = sorted((max(0, min(image_width - 1, x1)), max(1, min(image_width, x2))))
    y1, y2 = sorted((max(0, min(image_height - 1, y1)), max(1, min(image_height, y2))))
    if x2 <= x1 or y2 <= y1:
        st.warning("当前框选为空，无法生成裁剪预览。")
        return
    success, encoded = cv2.imencode(".png", image[y1:y2, x1:x2])
    if success:
        st.image(encoded.tobytes(), caption=f"裁剪预览 · 原始页坐标 [{x1}, {y1}, {x2}, {y2}]", width=width)


def _download_panel(document_result: dict) -> None:
    exports = document_result.get("exports") or {}
    st.subheader("结果导出")
    json_path = Path(exports.get("json") or "")
    csv_path = Path(exports.get("regions_csv") or "")
    sdf_path = Path(exports.get("structures_sdf") or "")
    smi_path = Path(exports.get("structures_smi") or "")
    detection_annotations_path = Path(exports.get("detection_annotations_json") or "")
    zip_path = Path(exports.get("zip") or "")
    if csv_path.is_file():
        st.download_button("CSV 区域结果", csv_path.read_bytes(), "regions.csv", "text/csv")
    structures = st.columns(2)
    if sdf_path.is_file():
        structures[0].download_button("SDF 结构合集", sdf_path.read_bytes(), "document_structures.sdf", "chemical/x-mdl-sdfile")
    if smi_path.is_file():
        structures[1].download_button("SMI 结构合集", smi_path.read_bytes(), "document_structures.smi", "chemical/x-daylight-smiles")
    if zip_path.is_file():
        st.caption(f"完整结果包已生成：{zip_path.name}（{zip_path.stat().st_size / 1024 / 1024:.2f} MB）")
        if st.button("准备完整结果包下载", key="prepare_document_zip_download"):
            st.session_state["document_zip_download_bytes"] = zip_path.read_bytes()
        if st.session_state.get("document_zip_download_bytes") is not None:
            st.download_button(
                "下载完整结果包",
                st.session_state["document_zip_download_bytes"],
                "document_results.zip",
                "application/zip",
            )
    with st.expander("高级导出", expanded=False):
        st.caption("JSON 与检测训练标注用于程序对接、审计和模型训练，不作为普通结果入口。")
        if json_path.is_file():
            st.download_button("JSON 完整审计数据", json_path.read_bytes(), "document_result.json", "application/json")
        if detection_annotations_path.is_file():
            st.download_button(
                "JSON 检测训练标注",
                detection_annotations_path.read_bytes(),
                "detection_annotations.json",
                "application/json",
            )
