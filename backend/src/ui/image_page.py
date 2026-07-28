"""Single-image recognition page."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import streamlit as st

import config
from src.analysis.correction import sha256_file
from src.analysis.molecule_report import MoleculeReportGenerator
from src.runtime.run_store import ImageRun, create_image_run_from_bytes, save_run_report, write_runtime_metadata
from src.storage.analysis_repository import record_report
from src.ui.image_editor import render_image_editor
from src.ui.image_viewer import show_upload_preview
from src.ui.report_view import show_report_export_actions, show_report_workbench
from src.ui.state import (
    current_runtime_key,
    remember_backend_status,
    runtime_config_from_key,
)
from src.ui.streamlit_compat import segmented_control
from src.ui.styles import page_intro
from src.runtime.job_manager import extract_json_object, run_json_command
from src.utils.file_utils import safe_stem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_WORKFLOW_STAGES = ["1 上传图片", "2 调整图片", "3 识别结果", "4 导出"]
IMAGE_WORKFLOW_STAGE_REQUEST_KEY = "image_workflow_requested_stage"


def render_image_page(backend: str, show_preprocessing: bool, export_pdf: bool) -> None:
    _apply_requested_workflow_stage()
    page_intro("图片识别", "上传单张分子结构图，执行 OCSR 识别、RDKit 校验、性质计算和人工纠错。")
    payload = st.session_state.get("single_image_upload_payload")
    default_stage = "3 识别结果" if st.session_state.get("image_report") else ("2 调整图片" if payload else "1 上传图片")
    stage = segmented_control(
        "单图识别流程",
        IMAGE_WORKFLOW_STAGES,
        default=st.session_state.get("image_workflow_stage", default_stage),
        key="image_workflow_stage",
        label_visibility="collapsed",
    )
    if stage != "1 上传图片" and not payload:
        st.info("请先上传一张分子结构图片。")
        _render_upload_step()
        return
    if stage in {"3 识别结果", "4 导出"} and "image_report" not in st.session_state:
        st.info("还没有识别结果。可以先在“调整图片”中启动识别。")
        _render_adjust_step(payload, backend)
        return

    if stage == "1 上传图片":
        _render_upload_step()
    elif stage == "2 调整图片":
        _render_adjust_step(payload, backend)
    elif stage == "3 识别结果":
        _render_result_step(show_preprocessing)
    elif stage == "4 导出":
        _render_export_step(export_pdf)


def _render_upload_step() -> None:
    uploaded = st.file_uploader("上传 PNG/JPG/JPEG 分子结构图", type=["png", "jpg", "jpeg"], key="single_upload")
    payload = st.session_state.get("single_image_upload_payload")
    if uploaded is not None:
        payload = _remember_uploaded_image(uploaded)
    if not payload:
        st.info("拖入或选择一张分子结构图片后，再进入调整和识别。")
        return
    left, right = st.columns([0.62, 0.38])
    with left:
        show_upload_preview(payload["bytes"], f"上传原图：{payload['name']}")
    with right:
        st.write(f"**文件名：** {payload['name']}")
        st.caption("文件哈希将在识别后的“技术详情”中记录。")
        if st.button("进入调整图片", type="primary", key="go_adjust_image"):
            _request_workflow_stage("2 调整图片")
            st.rerun()


def _render_adjust_step(payload: dict | None, backend: str) -> None:
    if not payload:
        return
    user_preprocessing, adjusted_bytes, has_adjustments = render_image_editor(
        payload["bytes"],
        payload["name"],
        key_prefix=f"single_{safe_stem(payload['name'])}",
        expanded=True,
        show_json=False,
    )
    st.session_state["single_image_adjustments"] = dict(user_preprocessing)
    st.session_state["single_image_adjusted_bytes"] = adjusted_bytes
    st.session_state["single_image_has_adjustments"] = bool(has_adjustments)
    actions = st.columns([0.24, 0.18, 0.58])
    if actions[0].button("开始识别与分析", type="primary", key="analyze_image"):
        _run_image_analysis(payload, backend, user_preprocessing, adjusted_bytes, has_adjustments)
    if actions[1].button("返回上传", key="back_to_image_upload"):
        _request_workflow_stage("1 上传图片")
        st.rerun()


def _render_result_step(show_preprocessing: bool) -> None:
    report = st.session_state.get("image_report")
    if not report:
        return
    key_prefix = f"image_{str(report.get('analysis_id', 'report'))[:8]}"
    active_report = show_report_workbench(report, show_preprocessing, key_prefix)
    st.session_state["image_report"] = active_report
    if st.button("进入导出", key="go_image_export"):
        _request_workflow_stage("4 导出")
        st.rerun()


def _render_export_step(export_pdf: bool) -> None:
    report = st.session_state.get("image_report")
    if not report:
        return
    st.subheader("结果导出")
    st.caption("复制 SMILES、下载结构文件或导出报告，不再默认展开所有格式。")
    key_prefix = f"image_{str(report.get('analysis_id', 'report'))[:8]}_export"
    show_report_export_actions(report, export_pdf, key_prefix)


def _remember_uploaded_image(uploaded: object) -> dict:
    uploaded_bytes = uploaded.getvalue()
    payload = {
        "name": uploaded.name,
        "bytes": uploaded_bytes,
        "sha256": sha256_file_like(uploaded_bytes),
    }
    previous = st.session_state.get("single_image_upload_payload") or {}
    if previous.get("sha256") != payload["sha256"]:
        st.session_state.pop("image_report", None)
        st.session_state.pop("single_image_adjustments", None)
        st.session_state.pop("single_image_adjusted_bytes", None)
        st.session_state.pop("single_image_has_adjustments", None)
    st.session_state["single_image_upload_payload"] = payload
    return payload


def sha256_file_like(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_workflow_stage(stage: str) -> None:
    """Queue a stage change for the next rerun, before the widget is created."""
    if stage in IMAGE_WORKFLOW_STAGES:
        st.session_state[IMAGE_WORKFLOW_STAGE_REQUEST_KEY] = stage


def _apply_requested_workflow_stage() -> None:
    """Apply a queued stage change before Streamlit instantiates the stage widget."""
    requested_stage = st.session_state.pop(IMAGE_WORKFLOW_STAGE_REQUEST_KEY, None)
    if requested_stage in IMAGE_WORKFLOW_STAGES:
        st.session_state["image_workflow_stage"] = requested_stage


def _run_image_analysis(
    payload: dict,
    backend: str,
    user_preprocessing: dict,
    adjusted_bytes: bytes,
    has_adjustments: bool,
) -> None:
    progress = st.status("正在准备图片…", expanded=True)
    progress.write("正在进行图像预处理。")
    progress.write("正在加载模型并执行 OCSR 识别；首次加载 MolScribe 模型通常需要几十秒。")
    image_run = create_image_run_from_bytes(payload["bytes"], payload["name"])
    try:
        effective_input = _prepare_effective_input(image_run, payload["name"], adjusted_bytes, user_preprocessing, has_adjustments)
        if backend == "demo":
            report = MoleculeReportGenerator(backend, image_run.run_dir).generate(
                image_path=effective_input,
                analysis_id=image_run.analysis_id,
            )
            _attach_user_preprocessing(report, user_preprocessing, effective_input, image_run.input_path, has_adjustments)
            result_path = save_run_report(report, image_run)
            record_report(report, result_path)
        else:
            report = _process_image_subprocess(image_run, backend, effective_input, user_preprocessing, has_adjustments)
            record_report(report, (report.get("run") or {}).get("report_path"))
        st.session_state["image_report"] = report
        _request_workflow_stage("3 识别结果")
        remember_backend_status(backend)
        progress.update(label="识别与分析完成", state="complete", expanded=False)
        st.rerun()
    except Exception as exc:
        write_runtime_metadata(image_run, {"status": "failed", "message": str(exc)})
        progress.update(label="识别未完成", state="error", expanded=True)
        st.error(str(exc))


def _process_image_subprocess(
    image_run: ImageRun,
    backend: str,
    effective_input: str | Path | None = None,
    user_preprocessing: dict | None = None,
    user_adjusted: bool = False,
) -> dict:
    """Run real OCSR outside Streamlit so native crashes do not kill the UI server."""
    runtime = runtime_config_from_key(current_runtime_key())
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "process_image.py"),
        "--input",
        str(image_run.input_path),
        "--backend",
        backend,
        "--original-filename",
        image_run.original_filename,
        "--analysis-id",
        image_run.analysis_id,
        "--run-dir",
        str(image_run.run_dir),
    ]
    if effective_input is not None and Path(effective_input).resolve() != image_run.input_path.resolve():
        command.extend(["--effective-input", str(effective_input)])
    if user_preprocessing is not None:
        payload = dict(user_preprocessing)
        payload["applied"] = bool(user_adjusted)
        command.extend(["--user-preprocessing-json", json.dumps(payload, ensure_ascii=False)])
    if runtime.get("molscribe_device"):
        command.extend(["--molscribe-device", str(runtime["molscribe_device"])])
    if runtime.get("decimer_device"):
        command.extend(["--decimer-device", str(runtime["decimer_device"])])
    if runtime.get("visible_gpu_index") is not None:
        command.extend(["--visible-gpu-index", str(runtime["visible_gpu_index"])])

    env = os.environ.copy()
    if backend == "ensemble":
        # MolScribe loads CUDA 11/cuDNN 8 while DECIMER uses TensorFlow with
        # CUDA 12/cuDNN 9. Keep them in their own model processes.
        env.pop("MOLSCRIBE_CHILD_PROCESS", None)
        env.pop("DECIMER_CHILD_PROCESS", None)
    else:
        # The report process is already isolated from Streamlit for a single
        # backend, so avoid a second model process in that case.
        env["MOLSCRIBE_CHILD_PROCESS"] = "1"
        env["DECIMER_CHILD_PROCESS"] = "1"
    completed = run_json_command(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=max(180.0, float(config.OCSR_TIMEOUT_SECONDS) + 60.0),
    )
    payload = completed.payload
    if completed.timed_out:
        raise RuntimeError("图像识别子进程超时，已终止后台进程。")
    if completed.returncode != 0:
        message = completed.last_output_line() or f"图像识别子进程退出码 {completed.returncode}"
        if payload and payload.get("message"):
            message = str(payload["message"])
        raise RuntimeError(f"图像识别子进程失败：{message}")
    if not payload or not payload.get("result_path"):
        raise RuntimeError("图像识别子进程未返回结果文件路径。")
    result_path = Path(str(payload["result_path"]))
    if not result_path.is_file():
        raise RuntimeError(f"图像识别结果文件不存在：{result_path}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _extract_json_object(text: str) -> dict | None:
    """Extract a JSON object from stdout that may also contain native-library logs."""
    return extract_json_object(text)


def _prepare_effective_input(
    image_run: ImageRun,
    original_filename: str,
    adjusted_bytes: bytes,
    user_preprocessing: dict,
    has_adjustments: bool,
) -> Path:
    if not has_adjustments:
        write_runtime_metadata(image_run, {"user_preprocessing": {**user_preprocessing, "applied": False}})
        return image_run.input_path
    stem = safe_stem(Path(original_filename).stem, "image")
    adjusted_path = image_run.input_dir / f"{stem}_user_adjusted.png"
    adjusted_path.write_bytes(adjusted_bytes)
    payload = {
        **user_preprocessing,
        "applied": True,
        "adjusted_image_path": str(adjusted_path.resolve()),
        "adjusted_image_sha256": sha256_file(adjusted_path),
    }
    user_preprocessing.clear()
    user_preprocessing.update(payload)
    write_runtime_metadata(image_run, {"user_preprocessing": payload})
    return adjusted_path


def _attach_user_preprocessing(
    report: dict,
    user_preprocessing: dict,
    effective_input: str | Path,
    original_input: str | Path,
    has_adjustments: bool,
) -> None:
    effective_path = Path(effective_input).expanduser().resolve()
    original_path = Path(original_input).expanduser().resolve()
    payload = {
        **user_preprocessing,
        "applied": bool(has_adjustments),
        "effective_image_path": str(effective_path),
        "effective_image_sha256": sha256_file(effective_path),
    }
    report["user_preprocessing"] = payload
    report.setdefault("input", {})
    report["input"]["effective_path"] = str(effective_path)
    report["input"]["effective_image_sha256"] = payload["effective_image_sha256"]
    if has_adjustments:
        report.setdefault("images", {}).setdefault("preprocessing", {})
        report["images"]["preprocessing"]["uploaded_original"] = str(original_path)
        report["images"]["preprocessing"]["user_adjusted"] = str(effective_path)
