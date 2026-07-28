"""Shared report and correction rendering."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from config import OUTPUT_DIR
from src.analysis.correction import (
    apply_ensemble_candidate_result,
    apply_strategy_attempt_result,
    apply_smiles_correction,
    confirm_structure,
    human_review_state,
    is_structure_confirmed,
    mark_structure_unable_to_confirm,
    restore_original_prediction,
    revoke_structure_confirmation,
    structure_similarity,
)
from src.chem.mol_drawer import draw_molecule
from src.chem.smiles_validator import validate_smiles
from src.export.json_exporter import to_json_text
from src.export.pdf_exporter import save_pdf
from src.export.structure_exporter import copyable_structure_fields, export_structure_files
from src.runtime.run_store import mark_run_protected_from_report, report_output_dir, save_report_for_existing_run
from src.storage.analysis_repository import AnalysisRepository, record_report
from src.ui.model_capability_view import capability_panel_data, model_result_status
from src.ui.image_viewer import show_preprocess_thumbnail, show_structure
from src.ui.labels import backend_label, status_label
from src.ui.records import render_records
from src.ui.streamlit_compat import segmented_control
from src.utils.file_utils import safe_stem


REPORT_SECTION_OPTIONS = ["概览", "确认与修正", "候选比较", "分子性质", "技术详情"]


def _final_smiles(report: dict[str, Any]) -> str | None:
    final = report.get("final") or {}
    ocsr = report.get("ocsr") or {}
    return final.get("smiles") or final.get("canonical_smiles") or ocsr.get("smiles")


def _format_local_time(value: Any) -> str:
    """Format an ISO timestamp in the machine's local timezone for the UI."""
    if not value:
        return "时间未记录"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError):
        return str(value)


def _has_second_candidate(report: dict[str, Any]) -> bool:
    """Return whether candidate comparison has two distinct structures."""
    ocsr = report.get("ocsr") or {}
    candidates = [item for item in (ocsr.get("candidates") or []) if isinstance(item, dict)]
    attempts = [
        item for item in (ocsr.get("strategy_attempts") or [])
        if isinstance(item, dict) and (item.get("smiles") or item.get("canonical_smiles"))
    ]
    distinct: set[str] = set()
    for item in [*candidates, *attempts]:
        smiles = next(
            (
                str(item.get(field)).strip()
                for field in ("canonical_smiles", "standardized_smiles", "smiles", "raw_smiles", "predicted_smiles")
                if item.get(field)
            ),
            "",
        )
        if not smiles:
            continue
        validation = validate_smiles(smiles)
        distinct.add(str(validation.get("canonical_smiles") or smiles))
    return len(distinct) >= 2


def _report_section_options(report: dict[str, Any]) -> list[str]:
    return [item for item in REPORT_SECTION_OPTIONS if item != "候选比较" or _has_second_candidate(report)]


def _persist_report_update(
    report: dict[str, Any],
    previous_report: dict[str, Any] | None = None,
    source: str | None = None,
    notes: str = "",
) -> None:
    report_path = save_report_for_existing_run(report)
    try:
        record_report(report, report_path)
        if previous_report is not None and source:
            previous_smiles = _final_smiles(previous_report)
            new_smiles = _final_smiles(report)
            if previous_smiles != new_smiles:
                AnalysisRepository().record_correction(
                    str(report.get("analysis_id") or ""),
                    previous_smiles,
                    new_smiles,
                    source,
                    notes,
                )
    except Exception:
        return


def _remember_report_update(report: dict[str, Any]) -> None:
    """Update matching report copies kept by Streamlit pages."""
    analysis_id = str(report.get("analysis_id") or "")
    matched = False
    for key in ("image_report", "history_report", "smiles_report"):
        existing = st.session_state.get(key)
        if not isinstance(existing, dict):
            continue
        if analysis_id and str(existing.get("analysis_id") or "") == analysis_id:
            st.session_state[key] = report
            matched = True
    if not matched:
        st.session_state["image_report"] = report


def _apply_report_update_and_rerun(report: dict[str, Any]) -> None:
    _remember_report_update(report)
    st.rerun()


def show_ensemble_details(ocsr: dict[str, Any]) -> None:
    candidates = ocsr.get("candidates") or []
    consensus = ocsr.get("consensus") or {}
    if not candidates and not consensus:
        return
    with st.expander("多后端候选与一致性", expanded=False):
        status = consensus.get("status") or "unknown"
        decision = consensus.get("decision") or "unknown"
        reason = consensus.get("reason") or ""
        if decision == "accepted" and status == "agreement":
            st.success(f"自动接受：{reason}")
        elif decision in {"accepted", "accepted_with_warning"}:
            st.warning(consensus.get("warning") or reason)
        elif decision == "review_needed":
            st.warning(consensus.get("warning") or reason or "需要人工确认。")
        elif decision == "rejected":
            st.error(reason or "无可靠候选。")
        else:
            st.info(reason or "暂无一致性结论。")
        st.caption(f"ensemble 决策：{decision}；候选状态：{status}")
        if candidates:
            rows = []
            for candidate in candidates:
                rows.append({
                    "后端": backend_label(candidate.get("backend"), short=True),
                    "状态": status_label(candidate.get("status")),
                    "原始 SMILES": candidate.get("raw_smiles"),
                    "Canonical SMILES": candidate.get("canonical_smiles"),
                    "有效": status_label(candidate.get("valid")),
                })
            render_records(rows, title_keys=("后端",))


def show_ensemble_candidate_actions(report: dict[str, Any]) -> dict[str, Any]:
    ocsr = report.get("ocsr") or {}
    candidates = ocsr.get("candidates") or []
    if not candidates:
        return report
    with st.expander("候选结果选择", expanded=False):
        current = report
        for candidate in candidates:
            current = _show_ensemble_candidate_card(current, candidate)
        return current


def _show_ensemble_candidate_card(report: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    backend = str(candidate.get("backend") or "unknown")
    smiles = candidate.get("raw_smiles") or ""
    canonical = candidate.get("canonical_smiles") or "-"
    similarity = _candidate_similarity_notes(candidate, (report.get("ocsr") or {}).get("similarity_analysis") or [])
    risk_hints = candidate.get("risk_hints") or []
    st.markdown(f"**{backend_label(backend, short=True)}**")
    left, right = st.columns([0.62, 0.38])
    with left:
        st.code(smiles or "-", language=None)
        st.caption(f"canonical: {canonical}")
        st.caption(f"置信度：{candidate.get('confidence') if candidate.get('confidence') is not None else '模型未提供'}")
        if similarity:
            st.caption("候选相似性：" + "；".join(similarity))
        if risk_hints:
            st.warning("风险提示：" + ", ".join(str(item) for item in risk_hints))
    with right:
        structure_path = _candidate_structure_path(report, candidate)
        if structure_path:
            show_structure(structure_path, f"{backend_label(backend, short=True)} 重绘结构")
    if candidate.get("valid") and smiles:
        analysis_id = report.get("analysis_id") or "image"
        if st.button(f"选为当前候选：{backend_label(backend, short=True)}", key=f"apply_candidate_{analysis_id}_{backend}"):
            updated = apply_ensemble_candidate_result(report, backend, report_output_dir(report, OUTPUT_DIR))
            error = (updated.get("correction") or {}).get("last_error")
            if error:
                st.warning(error)
            else:
                _persist_report_update(updated, report, f"user_selected_{backend}_candidate", f"采用 {backend} 候选结果")
                _apply_report_update_and_rerun(updated)
    st.divider()
    return report


def _candidate_similarity_notes(candidate: dict[str, Any], similarity_analysis: list[dict[str, Any]]) -> list[str]:
    backend = candidate.get("backend")
    notes: list[str] = []
    for item in similarity_analysis:
        if backend not in {item.get("backend_a"), item.get("backend_b")}:
            continue
        other = item.get("backend_b") if item.get("backend_a") == backend else item.get("backend_a")
        value = item.get("morgan_tanimoto")
        equality = "canonical一致" if item.get("canonical_smiles_equal") else "canonical不一致"
        notes.append(f"vs {other}: {equality}" if value is None else f"vs {other}: Tanimoto {value}, {equality}")
    return notes


def _candidate_structure_path(report: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    smiles = candidate.get("raw_smiles") or candidate.get("canonical_smiles")
    if not smiles or not candidate.get("valid"):
        return None
    analysis_id = safe_stem(str(report.get("analysis_id") or "analysis"), "analysis")
    backend = safe_stem(str(candidate.get("backend") or "candidate"), "candidate")
    path = report_output_dir(report, OUTPUT_DIR) / "candidate_structures" / f"{analysis_id}_{backend}.png"
    try:
        if path.is_file():
            return str(path.resolve())
        return draw_molecule(str(smiles), path)
    except Exception:
        return None


def show_strategy_attempts(report: dict[str, Any]) -> dict[str, Any]:
    ocsr = report.get("ocsr") or {}
    attempts = ocsr.get("strategy_attempts") or []
    if not attempts:
        return report
    with st.expander("多预处理策略尝试", expanded=False):
        selected = ocsr.get("selected_strategy") or "-"
        agreement = ocsr.get("strategy_agreement")
        st.caption(f"当前候选策略：{selected}；候选间结构{'一致' if agreement else '不一致' if agreement is False else '暂不可比较'}。")
        rows = []
        for attempt in attempts:
            rows.append({
                "strategy": attempt.get("strategy"),
                "status": status_label(attempt.get("status")),
                "smiles": attempt.get("smiles"),
                "canonical_smiles": attempt.get("canonical_smiles"),
                "valid_smiles": status_label(attempt.get("valid_smiles")),
                "confidence": attempt.get("confidence"),
            })
        render_records(
            rows,
            title_keys=("strategy",),
            summary_keys=("status", "canonical_smiles", "confidence"),
        )
        valid_options = [
            str(attempt.get("strategy"))
            for attempt in attempts
            if attempt.get("valid_smiles") and attempt.get("smiles")
        ]
        if valid_options:
            analysis_id = report.get("analysis_id") or "image"
            selected_option = st.selectbox(
                "选择预处理策略结果",
                valid_options,
                index=valid_options.index(str(selected)) if str(selected) in valid_options else 0,
                key=f"strategy_select_{analysis_id}",
            )
            if st.button("应用所选策略结果", key=f"apply_strategy_{analysis_id}"):
                updated = apply_strategy_attempt_result(report, selected_option, report_output_dir(report, OUTPUT_DIR))
                error = (updated.get("correction") or {}).get("last_error")
                if error:
                    st.warning(error)
                else:
                    _persist_report_update(updated, report, "strategy_selection", f"应用预处理策略 {selected_option}")
                    _apply_report_update_and_rerun(updated)
    return report


def show_chemical_identity(report: dict[str, Any]) -> None:
    identity = report.get("chemical_identity") or {}
    standardization = report.get("standardization") or {}
    warnings = report.get("structure_warnings") or []
    if not identity:
        return
    with st.expander("化学标准化与身份信息", expanded=False):
        metrics = st.columns(3)
        metrics[0].metric("片段数", identity.get("fragment_count") if identity.get("fragment_count") is not None else "-")
        metrics[1].metric("形式电荷", identity.get("formal_charge") if identity.get("formal_charge") is not None else "-")
        metrics[2].metric("结构提示", len(warnings))
        st.write(f"**Standardized SMILES：** `{identity.get('standardized_smiles') or '-'}`")
        st.write(f"**InChIKey：** `{identity.get('inchikey') or '当前不可用'}`")
        st.caption(
            f"标准化 profile：{standardization.get('profile') or '-'}；"
            f"是否改变：{'是' if standardization.get('changed') else '否'}。"
        )
        if warnings:
            normalized_warnings = [item if isinstance(item, dict) else {"提示": item} for item in warnings]
            render_records(normalized_warnings, title_keys=("type", "提示"))


def show_recognition_decision(report: dict[str, Any]) -> None:
    decision = report.get("recognition_decision") or {}
    if not decision:
        return
    value = decision.get("decision")
    message = decision.get("message") or ""
    risk = decision.get("risk_level") or "-"
    if value == "accepted":
        st.success(f"识别决策：自动接受（风险：{risk}）")
    elif value == "accepted_with_warning":
        st.warning(f"识别决策：带警告接受（风险：{risk}）")
    elif value == "review_needed":
        st.warning(f"识别决策：需要人工确认（风险：{risk}）")
    elif value == "rejected":
        st.error(f"识别决策：拒绝（风险：{risk}）")
    else:
        st.info(f"识别决策：{value or '未知'}")
    if message:
        st.caption(message)
    # Technical reason codes are intentionally kept out of the ordinary view.


def show_structure_exports(report: dict[str, Any], key_prefix: str) -> None:
    """Render copyable chemistry identifiers and structure downloads."""
    try:
        fields = copyable_structure_fields(report)
        export_dir = report_output_dir(report, OUTPUT_DIR) / "structure_exports"
        exports = export_structure_files(report, export_dir, prefix=key_prefix)
    except Exception as exc:
        st.caption(f"化学格式导出不可用：{exc}")
        return

    labels = {
        "original_smiles": "复制原始 SMILES",
        "canonical_smiles": "复制 Canonical SMILES",
        "inchi": "复制 InChI",
        "inchikey": "复制 InChIKey",
    }
    columns = st.columns(2)
    for index, (field, label) in enumerate(labels.items()):
        with columns[index % 2]:
            st.text_input(label, value=fields.get(field) or "", key=f"{key_prefix}_{field}_copy")

    download_specs = [
        ("下载 MOL", "mol", "chemical/x-mdl-molfile"),
        ("下载 SDF", "sdf", "chemical/x-mdl-sdfile"),
        ("下载 SVG", "svg", "image/svg+xml"),
        ("下载 PNG", "png", "image/png"),
        ("下载完整 ZIP", "zip", "application/zip"),
    ]
    buttons = st.columns(3)
    for index, (label, field, mime) in enumerate(download_specs):
        path = Path(exports[field])
        with buttons[index % 3]:
            st.download_button(
                label,
                path.read_bytes(),
                file_name=path.name,
                mime=mime,
                key=f"{key_prefix}_{field}_download",
            )


def show_model_trust_and_capabilities(report: dict[str, Any]) -> None:
    """Show execution, parsing, and verification as separate production states."""
    if not report.get("production_routing"):
        return
    status = model_result_status(report)
    st.subheader(status["candidate_role"])
    if status["candidate_smiles"]:
        st.code(status["candidate_smiles"], language=None)
    states = st.columns(3)
    states[0].metric("Backend execution succeeded", "Yes" if status["backend_execution_succeeded"] else "No")
    states[1].metric("Valid SMILES produced", "Yes" if status["valid_smiles_produced"] else "No")
    states[2].metric("Structure verified", "Yes" if status["structure_verified"] else "No")
    st.warning(status["prediction_notice"])
    if status["requires_review"]:
        st.warning("Requires review")
    if status["agreement_status"] == "agreement":
        st.info("Model agreement was recorded, but it does not increase the verification level.")
    if status["risk_flags"]:
        st.error("High-risk structure flags: " + ", ".join(status["risk_flags"]))

    capabilities = capability_panel_data()
    with st.expander("模型能力与限制 / Model capabilities and limitations", expanded=False):
        defaults = capabilities["production_defaults"]
        st.write(
            f"Current model: DECIMER primary (profile={defaults['decimer_profile']}); "
            "MolScribe fallback candidate; Experimental ensemble disabled by default."
        )
        st.write(f"Dataset: {capabilities['dataset']} ({capabilities['dataset_role']})")
        for name in ("decimer", "molscribe"):
            model = capabilities["models"][name]
            st.write(
                f"{name.upper()}: connectivity={model['connectivity_exact']:.4f}, "
                f"full InChIKey={model['full_inchikey_exact']:.4f}, "
                f"canonical={model['canonical_exact']:.4f}, parse={model['parse_rate']:.4f}; "
                f"fine tuning={model['fine_tuning_status']}."
            )
        st.warning(capabilities["style_notice"])
        st.warning("Reliable local fine-tuning is not currently supported in this project environment.")
        st.warning(capabilities["scope_notice"])


def _render_result_message(report: dict[str, Any], ocsr: dict[str, Any]) -> None:
    if (report.get("input") or {}).get("type") == "image" and not is_structure_confirmed(report):
        st.info("已生成候选结构。请对照原图确认、修改 SMILES，或标记为无法确认。")
    else:
        st.success(report.get("message", "分析完成。"))
    if ocsr.get("backend") == "demo":
        st.warning("当前是演示结果：系统按内置样例文件名返回固定 SMILES，并没有进行真实图片识别。")
    elif ocsr.get("result_origin") in {"real_model", "real_model_ensemble"}:
        st.caption("当前结果来自真实 OCSR 模型推理。")


def _render_core_result(report: dict[str, Any]) -> None:
    ocsr = report.get("ocsr") or {}
    correction = report.get("correction") or {}
    final = report.get("final") or {}
    validation = report.get("validation") or {}
    confirmed = is_structure_confirmed(report)
    structure_label = "最终结构" if confirmed else "候选结构"
    original_path = _original_image_path(report)
    left, right = st.columns(2)
    with left:
        st.subheader("原始图片")
        if original_path:
            st.image(str(original_path), caption="上传原图", width=480)
        else:
            st.info("原始图片文件当前不可用。")
    with right:
        st.subheader(f"{structure_label}重绘图")
        show_structure((report.get("images") or {}).get("redrawn_molecule"), f"{structure_label}（RDKit 重绘）")

    st.markdown(f"#### {structure_label}")
    st.code(final.get("smiles") or ocsr.get("smiles") or "", language=None)
    st.write(f"**Canonical SMILES：** `{final.get('canonical_smiles') or validation.get('canonical_smiles') or '-'}`")
    st.write(f"**RDKit 校验：** {'有效' if validation.get('valid') else '无效'}")
    confidence = ocsr.get("confidence")
    st.caption(f"模型置信度：{confidence if confidence is not None else '模型未提供'}；该数值不等同于人工确认。")
    if correction.get("applied"):
        st.info("当前候选已应用人工输入的 SMILES；模型原始预测仍保留在技术详情和修改面板中。")


def _original_image_path(report: dict[str, Any]) -> Path | None:
    preprocessing = (report.get("images") or {}).get("preprocessing") or {}
    candidates = [
        preprocessing.get("uploaded_original"),
        (report.get("input") or {}).get("path"),
        preprocessing.get("original"),
    ]
    for candidate in candidates:
        if candidate and Path(str(candidate)).is_file():
            return Path(str(candidate)).resolve()
    return None


def _render_human_review_actions(report: dict[str, Any]) -> dict[str, Any]:
    """Render the three explicit single-image review actions."""
    review = human_review_state(report)
    correction = report.get("correction") or {}
    analysis_id = str(report.get("analysis_id") or "image")
    status = review.get("status")
    states = st.columns(2)
    states[0].metric("确认状态", "已确认" if status == "confirmed" else "无法确认" if status == "unable_to_confirm" else "未确认")
    states[1].metric("修正状态", "已修正" if correction.get("applied") else "未修正")
    if status == "confirmed":
        st.success(f"已确认 · {_format_local_time(review.get('reviewed_at'))}")
    elif status == "unable_to_confirm":
        st.error("当前结构无法人工确认。正式报告和结构文件仍被锁定。")
    else:
        st.warning("当前仅为候选结构，尚未经过人工确认。")

    if status == "confirmed":
        actions = st.columns(2)
        if actions[0].button("撤销确认", key=f"revoke_confirmation_{analysis_id}"):
            updated = revoke_structure_confirmation(report)
            _persist_report_update(updated, report, "revoke_confirmation", "用户撤销结构确认")
            _apply_report_update_and_rerun(updated)
        with actions[1].popover("重新修改结构"):
            updated = _render_live_smiles_editor(report, analysis_id)
            if updated is not report:
                return updated
        return report

    actions = st.columns(3)
    if actions[0].button(
        "确认结构正确",
        type="primary",
        key=f"confirm_structure_{analysis_id}",
        disabled=status == "confirmed" or not (report.get("validation") or {}).get("valid"),
    ):
        updated = confirm_structure(report)
        error = human_review_state(updated).get("last_error")
        if error:
            st.error(str(error))
        else:
            _persist_report_update(updated, report, "confirm_structure", "用户确认结构正确")
            _apply_report_update_and_rerun(updated)

    with actions[1].popover("修改 SMILES"):
        updated = _render_live_smiles_editor(report, analysis_id)
        if updated is not report:
            return updated

    if actions[2].button("无法确认", key=f"unable_to_confirm_{analysis_id}"):
        updated = mark_structure_unable_to_confirm(report)
        _persist_report_update(updated, report, "unable_to_confirm", "用户无法确认候选结构")
        _apply_report_update_and_rerun(updated)
    return report


def _render_live_smiles_editor(report: dict[str, Any], analysis_id: str) -> dict[str, Any]:
    ocsr = report.get("ocsr") or {}
    correction = report.get("correction") or {}
    final = report.get("final") or {}
    predicted = ocsr.get("predicted_smiles") or ocsr.get("smiles") or ""
    current = correction.get("corrected_smiles") or final.get("raw_smiles") or final.get("smiles") or predicted
    st.text_input("模型原始预测（保留）", value=predicted, disabled=True, key=f"original_prediction_{analysis_id}")
    edited = st.text_input(
        "候选 SMILES",
        value=current,
        key=f"live_corrected_smiles_{analysis_id}",
        help="输入变化后立即执行 RDKit 校验、canonical 化和结构重绘。",
    )
    validation = validate_smiles(edited)
    if not validation.get("valid"):
        st.error(str(validation.get("error") or "SMILES 无效。"))
        return report

    canonical = str(validation.get("canonical_smiles") or "")
    st.success("RDKit 校验通过")
    st.code(canonical, language=None)
    preview_dir = report_output_dir(report, OUTPUT_DIR) / "candidate_previews"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    preview_path = preview_dir / f"{safe_stem(analysis_id)}_{digest}.png"
    try:
        if not preview_path.is_file():
            draw_molecule(canonical, preview_path)
        show_structure(preview_path, "实时候选结构预览")
    except Exception as exc:
        st.error(f"结构重绘失败：{exc}")
        return report

    if st.button("应用为候选结构", type="primary", key=f"apply_live_correction_{analysis_id}"):
        updated = apply_smiles_correction(report, edited, report_output_dir(report, OUTPUT_DIR))
        error = (updated.get("correction") or {}).get("last_error")
        if error:
            st.error(str(error))
            return report
        _persist_report_update(updated, report, "user_correction", "用户修改候选 SMILES")
        _apply_report_update_and_rerun(updated)
        return updated
    return report


def _render_descriptor_metrics(report: dict[str, Any]) -> None:
    descriptors = report.get("descriptors") or {}
    if not descriptors:
        return
    confirmed = is_structure_confirmed(report)
    st.subheader("分子性质" if confirmed else "候选性质预览")
    if not confirmed:
        st.error("免责声明：以下性质由尚未人工确认的候选结构计算，仅供预览，不得作为正式分析结论。")
    labels = {
        "formula": "分子式",
        "molecular_weight": "分子量",
        "logp": "LogP",
        "tpsa": "TPSA",
        "hbd": "HBD",
        "hba": "HBA",
        "rotatable_bonds": "可旋转键",
        "heavy_atom_count": "重原子数",
    }
    compact = st.columns(min(4, max(1, len(descriptors))))
    for index, (key, value) in enumerate(descriptors.items()):
        compact[index % len(compact)].metric(labels.get(key, key), str(value))


def _render_lipinski_summary(report: dict[str, Any]) -> None:
    lipinski = report.get("lipinski") or {}
    if not lipinski:
        return
    if lipinski.get("passed"):
        st.info(lipinski.get("summary", "符合规则。"))
    else:
        st.warning(lipinski.get("summary", "存在规则提示。"))


def _render_preprocessing_details(report: dict[str, Any], show_preprocessing: bool) -> None:
    if not show_preprocessing or report.get("input", {}).get("type") != "image":
        st.info("预处理过程默认隐藏；可在侧栏勾选“显示高级信息 / 显示 OpenCV 预处理过程”后查看。")
        return
    if report.get("user_preprocessing"):
        with st.expander("人工预处理参数", expanded=False):
            st.json({"user_preprocessing": report.get("user_preprocessing")})
    stage_paths = (report.get("images") or {}).get("preprocessing") or {}
    if not stage_paths:
        st.info("该报告没有预处理图像记录。")
        return
    titles = {
        "uploaded_original": "上传原图",
        "user_adjusted": "人工调整图",
        "original": "原图",
        "gray": "灰度",
        "denoised": "去噪",
        "binary": "二值化",
        "cropped": "裁剪",
        "deskewed": "旋转校正",
        "normalized": "归一化",
    }
    columns = st.columns(3)
    stage_order = [
        "uploaded_original",
        "user_adjusted",
        "original",
        "gray",
        "denoised",
        "binary",
        "cropped",
        "deskewed",
        "normalized",
    ]
    for index, name in enumerate(stage_order):
        if name in stage_paths:
            with columns[index % 3]:
                show_preprocess_thumbnail(stage_paths[name], titles[name])


def _compact_technical(value: Any, key: str = "") -> Any:
    """Remove empty technical values and express recorded durations in seconds."""
    if value is None or (isinstance(value, str) and (not value.strip() or value.strip().lower() == "none")):
        return None
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for child_key, child_value in value.items():
            output_key = str(child_key)
            converted = child_value
            if output_key.endswith("_time_ms") and isinstance(child_value, (int, float)) and not isinstance(child_value, bool):
                output_key = output_key[:-2] + "seconds"
                converted = round(float(child_value) / 1000.0, 3)
            cleaned = _compact_technical(converted, output_key)
            if cleaned is not None and cleaned != {} and cleaned != []:
                compact[output_key] = cleaned
        return compact or None
    if isinstance(value, (list, tuple)):
        compact_list = [_compact_technical(item, key) for item in value]
        compact_list = [item for item in compact_list if item is not None and item != {} and item != []]
        return compact_list or None
    return value


def _render_technical_diagnostics(report: dict[str, Any]) -> None:
    ocsr = report.get("ocsr") or {}
    runtime = report.get("runtime") or {}
    inference_ms = ocsr.get("inference_time_ms")
    payload = _compact_technical({
        "backend": ocsr.get("backend"),
        "device": ocsr.get("device"),
        "model_name": ocsr.get("model_name"),
        "model_version": ocsr.get("model_version"),
        "model_sha256": ocsr.get("model_sha256"),
        "package_version": ocsr.get("package_version"),
        "git_commit": ocsr.get("git_commit") or runtime.get("git_commit"),
        "app_mode": runtime.get("app_mode"),
        "dependency_versions": ocsr.get("dependency_versions") or runtime.get("dependency_versions"),
        "inference_time_seconds": round(float(inference_ms) / 1000.0, 3)
        if isinstance(inference_ms, (int, float)) and not isinstance(inference_ms, bool)
        else None,
        "created_at_utc": report.get("created_at"),
        "reviewed_at_utc": human_review_state(report).get("reviewed_at"),
        "corrected_at_utc": (report.get("correction") or {}).get("corrected_at"),
        "recognition_decision": report.get("recognition_decision"),
        "production_routing": report.get("production_routing"),
        "recognition_audit": report.get("recognition_audit"),
        "human_review": human_review_state(report),
        "review_events": report.get("review_events"),
        "image_sha256": (report.get("input") or {}).get("image_sha256"),
        "ocsr_full": ocsr,
    })
    st.json(payload or {})


def show_report_export_actions(report: dict[str, Any], export_pdf: bool, key_prefix: str) -> None:
    """Render compact report export actions without occupying the whole page."""
    if (report.get("input") or {}).get("type") == "image":
        _show_single_image_exports(report, key_prefix)
        return
    final = report.get("final") or {}
    validation = report.get("validation") or {}
    smiles = final.get("smiles") or (report.get("ocsr") or {}).get("smiles") or ""
    canonical = final.get("canonical_smiles") or validation.get("canonical_smiles") or ""
    actions = st.columns([0.18, 0.20, 0.20, 0.42])
    with actions[0].popover("复制 SMILES"):
        st.text_input("SMILES", value=smiles, key=f"{key_prefix}_smiles_copy")
        st.text_input("Canonical SMILES", value=canonical, key=f"{key_prefix}_canonical_copy")
    with actions[1].popover("下载结构"):
        show_structure_exports(report, key_prefix)
    with actions[2].popover("导出报告"):
        st.download_button(
            "下载 JSON 报告",
            to_json_text(report),
            file_name=f"{key_prefix}_report.json",
            mime="application/json",
            key=f"json_{key_prefix}",
        )
        if export_pdf:
            pdf_result = save_pdf(report, OUTPUT_DIR / f"{key_prefix}_report.pdf")
            if pdf_result["success"]:
                st.download_button(
                    "下载 PDF 报告",
                    Path(pdf_result["path"]).read_bytes(),
                    file_name=f"{key_prefix}_report.pdf",
                    mime="application/pdf",
                    key=f"pdf_{key_prefix}",
                )
            else:
                st.caption(pdf_result["message"])
        else:
            st.caption("如需 PDF，请在侧栏高级信息中启用 PDF 报告。")
    if report.get("run") and actions[3].button("保留本次分析记录", key=f"protect_run_{key_prefix}"):
        mark_run_protected_from_report(report, "user_keep")
        st.success("已标记保留；自动清理不会删除该分析记录。")


def _show_single_image_exports(report: dict[str, Any], key_prefix: str) -> None:
    """Enforce candidate/formal export permissions for single-image reports."""
    confirmed = is_structure_confirmed(report)
    review = human_review_state(report)
    pdf_kind = "正式" if confirmed else "候选"
    if confirmed:
        st.success("结构已人工确认：可导出正式 PDF、SMI、MOL 和 SDF。")
    else:
        status_text = "无法确认" if review.get("status") == "unable_to_confirm" else "未人工确认"
        st.warning(f"当前状态：{status_text}。只能导出带“未人工确认”水印的候选 PDF。")

    pdf_path = report_output_dir(report, OUTPUT_DIR) / f"{safe_stem(key_prefix)}_{'formal' if confirmed else 'candidate'}_report.pdf"
    pdf_result = save_pdf(report, pdf_path)
    if pdf_result.get("success"):
        pdf_label = "下载正式 PDF" if confirmed else "下载候选 PDF（未人工确认）"
        st.download_button(
            pdf_label,
            Path(str(pdf_result["path"])).read_bytes(),
            file_name=Path(str(pdf_result["path"])).name,
            mime="application/pdf",
            key=f"image_pdf_{key_prefix}_{pdf_kind}",
        )
    else:
        st.caption(str(pdf_result.get("message") or "PDF 生成失败。"))

    if confirmed:
        final = report.get("final") or {}
        validation = report.get("validation") or {}
        smiles = str(final.get("smiles") or validation.get("canonical_smiles") or "")
        try:
            fields = copyable_structure_fields(report)
            copy_columns = st.columns(3)
            copy_columns[0].text_input(
                "复制最终 SMILES",
                value=smiles,
                key=f"copy_final_smiles_{key_prefix}",
            )
            copy_columns[1].text_input(
                "复制 Canonical SMILES",
                value=str(fields.get("canonical_smiles") or ""),
                key=f"copy_canonical_smiles_{key_prefix}",
            )
            copy_columns[2].text_input(
                "复制 InChIKey",
                value=str(fields.get("inchikey") or ""),
                key=f"copy_inchikey_{key_prefix}",
            )
            exports = export_structure_files(
                report,
                report_output_dir(report, OUTPUT_DIR) / "formal_structure_exports",
                prefix=f"{safe_stem(key_prefix)}_confirmed",
            )
            specs = [
                ("下载 SMI", smiles.encode("utf-8") + b"\n", f"{safe_stem(key_prefix)}_confirmed.smi", "chemical/x-daylight-smiles"),
                ("下载 MOL", Path(exports["mol"]).read_bytes(), Path(exports["mol"]).name, "chemical/x-mdl-molfile"),
                ("下载 SDF", Path(exports["sdf"]).read_bytes(), Path(exports["sdf"]).name, "chemical/x-mdl-sdfile"),
            ]
            columns = st.columns(3)
            for index, (label, data, filename, mime) in enumerate(specs):
                columns[index].download_button(
                    label,
                    data,
                    file_name=filename,
                    mime=mime,
                    key=f"formal_{index}_{key_prefix}",
                )
        except Exception as exc:
            st.caption(f"正式结构文件导出不可用：{exc}")

    if report.get("run") and st.button("保留本次分析记录", key=f"protect_run_{key_prefix}"):
        mark_run_protected_from_report(report, "user_keep")
        st.success("已标记保留；自动清理不会删除该分析记录。")


def show_report_workbench(report: dict[str, Any], show_preprocessing: bool, key_prefix: str) -> dict[str, Any]:
    """Render a compact, sectioned report view for the single-image workflow."""
    if report.get("status") != "success":
        show_report(report, show_preprocessing, False, key_prefix)
        return report

    ocsr = report.get("ocsr") or {}
    _render_result_message(report, ocsr)
    report = _render_human_review_actions(report)
    section_options = _report_section_options(report)
    section_key = f"{key_prefix}_report_section"
    requested_section = st.session_state.get(section_key, "概览")
    if requested_section not in section_options:
        requested_section = "概览"
        st.session_state[section_key] = requested_section
    section = segmented_control(
        "结果视图",
        section_options,
        default=requested_section if requested_section in section_options else "概览",
        key=section_key,
        label_visibility="collapsed",
    )
    if section == "概览":
        _render_core_result(report)
        _render_descriptor_metrics(report)
        _render_lipinski_summary(report)
    elif section == "确认与修正":
        report = show_correction_panel(report)
    elif section == "候选比较":
        show_ensemble_details(ocsr)
        report = show_ensemble_candidate_actions(report)
        report = show_strategy_attempts(report)
    elif section == "分子性质":
        _render_descriptor_metrics(report)
        _render_lipinski_summary(report)
        show_chemical_identity(report)
    elif section == "技术详情":
        _render_preprocessing_details(report, show_preprocessing)
        _render_technical_diagnostics(report)
    return report


def show_report(report: dict[str, Any], show_preprocessing: bool, export_pdf: bool, key_prefix: str) -> None:
    """Render a molecule analysis report in Streamlit."""
    if report.get("status") != "success":
        ocsr = report.get("ocsr") or {}
        show_model_trust_and_capabilities(report)
        consensus = ocsr.get("consensus") or {}
        routing = report.get("production_routing") or {}
        if consensus.get("decision") == "review_needed" or routing.get("review_required"):
            st.warning(report.get("message", "多个后端结果不一致，需要人工确认。"))
        else:
            st.error(report.get("message", "分析失败。"))
        if ocsr:
            st.caption(f"后端：{backend_label(ocsr.get('backend'), short=True)}；状态：{status_label(ocsr.get('status'))}")
            if ocsr.get("backend") == "demo":
                st.warning("这是演示后端，不会识别任意图片；请使用真实后端或手动输入 SMILES。")
            show_recognition_decision(report)
            show_ensemble_details(ocsr)
            report = show_ensemble_candidate_actions(report)
            show_strategy_attempts(report)
        return

    ocsr = report.get("ocsr") or {}
    show_model_trust_and_capabilities(report)
    correction = report.get("correction") or {}
    final = report.get("final") or {}
    validation = report.get("validation") or {}
    st.success(report.get("message", "分析完成。"))
    if ocsr.get("backend") == "demo":
        st.warning("当前是演示结果：系统按内置样例文件名返回固定 SMILES，并没有进行真实图片识别。")
    elif ocsr.get("result_origin") in {"real_model", "real_model_ensemble"}:
        st.caption("当前结果来自真实 OCSR 模型推理。")
    show_recognition_decision(report)

    left, right = st.columns([1.1, 0.9])
    with left:
        st.subheader("核心识别结果")
        st.code(final.get("smiles") or ocsr.get("smiles") or "", language=None)
        st.write(f"**Canonical SMILES：** `{final.get('canonical_smiles') or validation.get('canonical_smiles') or '-'}`")
        if final.get("standardized_smiles") or validation.get("standardized_smiles"):
            st.write(f"**Standardized SMILES：** `{final.get('standardized_smiles') or validation.get('standardized_smiles')}`")
        confidence = ocsr.get("confidence")
        st.write(f"**识别后端：** {backend_label(ocsr.get('backend'), short=True)}")
        st.write(f"**置信度：** {confidence if confidence is not None else '模型未提供'}")
        st.write(f"**当前结果来源：** {final.get('source') or '未知'}")
        st.write(f"**RDKit 校验：** {'有效' if validation.get('valid') else '无效'}")
        if correction.get("applied"):
            st.info(f"已应用人工修正：`{correction.get('corrected_canonical_smiles')}`")
    with right:
        st.subheader("分子结构重绘")
        show_structure((report.get("images") or {}).get("redrawn_molecule"), "最终分析结构")

    descriptors = report.get("descriptors") or {}
    if descriptors:
        st.subheader("分子性质")
        labels = {
            "formula": "分子式",
            "molecular_weight": "分子量",
            "logp": "LogP",
            "tpsa": "TPSA",
            "hbd": "HBD",
            "hba": "HBA",
            "rotatable_bonds": "可旋转键",
            "heavy_atom_count": "重原子数",
        }
        compact = st.columns(min(4, max(1, len(descriptors))))
        for index, (key, value) in enumerate(descriptors.items()):
            compact[index % len(compact)].metric(labels.get(key, key), str(value))

    lipinski = report.get("lipinski") or {}
    if lipinski:
        if lipinski.get("passed"):
            st.info(lipinski.get("summary", "符合规则。"))
        else:
            st.warning(lipinski.get("summary", "存在规则提示。"))

    show_ensemble_details(ocsr)
    report = show_ensemble_candidate_actions(report)
    ocsr = report.get("ocsr") or {}
    report = show_strategy_attempts(report)
    ocsr = report.get("ocsr") or {}
    show_chemical_identity(report)

    if show_preprocessing and report.get("input", {}).get("type") == "image":
        with st.expander("OpenCV 预处理过程", expanded=False):
            if report.get("user_preprocessing"):
                st.json({"user_preprocessing": report.get("user_preprocessing")})
            stage_paths = (report.get("images") or {}).get("preprocessing") or {}
            titles = {
                "uploaded_original": "上传原图",
                "user_adjusted": "人工调整图",
                "original": "原图",
                "gray": "灰度",
                "denoised": "去噪",
                "binary": "二值化",
                "cropped": "裁剪",
                "deskewed": "旋转校正",
                "normalized": "归一化",
            }
            columns = st.columns(3)
            stage_order = [
                "uploaded_original",
                "user_adjusted",
                "original",
                "gray",
                "denoised",
                "binary",
                "cropped",
                "deskewed",
                "normalized",
            ]
            for index, name in enumerate(stage_order):
                if name in stage_paths:
                    with columns[index % 3]:
                        show_preprocess_thumbnail(stage_paths[name], titles[name])

    with st.expander("结果导出", expanded=False):
        show_report_export_actions(report, export_pdf, key_prefix)

    with st.expander("技术诊断", expanded=False):
        runtime = report.get("runtime") or {}
        inference_ms = ocsr.get("inference_time_ms")
        diagnostics = _compact_technical({
            "backend": ocsr.get("backend"),
            "device": ocsr.get("device"),
            "model_name": ocsr.get("model_name"),
            "model_version": ocsr.get("model_version"),
            "model_sha256": ocsr.get("model_sha256"),
            "package_version": ocsr.get("package_version"),
            "git_commit": ocsr.get("git_commit") or runtime.get("git_commit"),
            "app_mode": runtime.get("app_mode"),
            "dependency_versions": ocsr.get("dependency_versions") or runtime.get("dependency_versions"),
            "inference_time_seconds": round(float(inference_ms) / 1000.0, 3)
            if isinstance(inference_ms, (int, float)) and not isinstance(inference_ms, bool)
            else None,
            "created_at_utc": report.get("created_at"),
        })
        st.json(diagnostics or {})


def show_correction_panel(report: dict[str, Any]) -> dict[str, Any]:
    """Render confirmation and correction controls for image reports."""
    if (report.get("input") or {}).get("type") != "image":
        return report
    analysis_id = report.get("analysis_id") or "image"
    ocsr = report.get("ocsr") or {}
    correction = report.get("correction") or {}
    final = report.get("final") or {}
    predicted = ocsr.get("predicted_smiles") or ocsr.get("smiles") or ""
    default_smiles = correction.get("corrected_smiles") or final.get("smiles") or predicted

    review = human_review_state(report)
    st.subheader("确认与修正")
    states = st.columns(2)
    states[0].metric(
        "确认状态",
        "已确认" if review.get("status") == "confirmed" else "无法确认" if review.get("status") == "unable_to_confirm" else "未确认",
    )
    states[1].metric("修正状态", "已修正" if correction.get("applied") else "未修正")
    if review.get("reviewed_at"):
        st.caption(f"审核时间：{_format_local_time(review.get('reviewed_at'))}")
    if correction.get("corrected_at"):
        st.caption(f"修正时间：{_format_local_time(correction.get('corrected_at'))}")
    st.caption(f"当前结果来源：{final.get('source') or '暂无有效结果'}")
    st.text_input("模型原始预测", value=predicted, disabled=True, key=f"predicted_{analysis_id}")
    corrected_input = st.text_input(
        "修正 SMILES",
        value=default_smiles or "",
        key=f"corrected_smiles_{analysis_id}",
        placeholder="OCSR 失败时也可以手动输入 SMILES",
    )
    apply_col, restore_col = st.columns(2)
    current_report = report
    if apply_col.button("校验并应用修正", type="primary", key=f"apply_correction_{analysis_id}"):
        candidate = apply_smiles_correction(report, corrected_input, report_output_dir(report, OUTPUT_DIR))
        error = (candidate.get("correction") or {}).get("last_error")
        if error:
            st.error(error)
        else:
            current_report = candidate
            _persist_report_update(current_report, report, "user_correction", "用户手动修正 SMILES")
            st.session_state["image_report"] = current_report
            st.success("人工修正已应用，性质和结构图已重新生成。")
    if restore_col.button("恢复模型原始结果", key=f"restore_prediction_{analysis_id}"):
        candidate = restore_original_prediction(report, report_output_dir(report, OUTPUT_DIR))
        error = (candidate.get("correction") or {}).get("last_error")
        if error:
            st.warning(error)
        else:
            current_report = candidate
            _persist_report_update(current_report, report, "restore_original_prediction", "恢复模型原始结果")
            st.session_state["image_report"] = current_report
            st.success("已恢复为模型原始预测。")

    images = current_report.get("images") or {}
    predicted_image = images.get("predicted_molecule")
    corrected_image = images.get("corrected_molecule")
    if predicted_image or corrected_image:
        st.subheader("结构对比")
        columns = st.columns(2) if predicted_image and corrected_image else st.columns(1)
        if predicted_image:
            with columns[0]:
                show_structure(predicted_image, "模型预测结构")
        if corrected_image:
            target = columns[1] if predicted_image and corrected_image else columns[0]
            with target:
                show_structure(corrected_image, "人工修正结构")
        if predicted and (current_report.get("correction") or {}).get("corrected_smiles"):
            similarity = structure_similarity(predicted, (current_report.get("correction") or {}).get("corrected_smiles"))
            if similarity is not None:
                st.caption(f"Morgan Tanimoto 相似度：{similarity:.3f}。该值只比较两个分子结果，不代表与原图一致。")
    return current_report
