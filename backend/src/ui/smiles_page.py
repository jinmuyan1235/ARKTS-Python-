"""Interactive single and batch SMILES analysis page."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import streamlit as st

from config import OUTPUT_DIR
from src.analysis.correction import structure_similarity
from src.analysis.molecule_report import MoleculeReportGenerator
from src.analysis.smiles_workflow import (
    csv_bytes,
    parse_smiles_text,
    parse_smiles_upload,
    report_to_smiles_row,
    single_smiles_export_row,
    smiles_batch_exports,
)
from src.chem.smiles_validator import diagnose_smiles
from src.export.json_exporter import save_json
from src.export.pdf_exporter import save_pdf
from src.export.structure_exporter import copyable_structure_fields, mol_text, report_structure_smiles, sdf_text
from src.storage.analysis_repository import AnalysisRepository, record_report
from src.ui.styles import page_intro


MAX_BATCH_SMILES = 2000


@st.cache_data(show_spinner=False, max_entries=512)
def _cached_manual_report(smiles: str) -> dict[str, Any]:
    """Cache duplicate RDKit work while keeping manual reports fully compatible."""
    return MoleculeReportGenerator("manual", OUTPUT_DIR / "smiles_live").generate(smiles=smiles)


def render_smiles_page(export_pdf: bool) -> None:
    page_intro(
        "SMILES 分析",
        "不调用 OCSR 模型；支持实时 RDKit 校验、标准化、结构对比、批量文件和正式结构导出。",
    )
    _initialize_single_state()
    single_tab, batch_tab, history_tab = st.tabs(["单条分析", "批量分析", "最近分析历史"])
    with single_tab:
        _render_single_analysis(export_pdf)
    with batch_tab:
        _render_batch_analysis()
    with history_tab:
        _render_recent_history()


def _initialize_single_state() -> None:
    st.session_state.setdefault("smiles_single_original", "CCO")
    st.session_state.setdefault("smiles_single_editor", st.session_state["smiles_single_original"])


def _sync_single_editor() -> None:
    st.session_state["smiles_single_editor"] = st.session_state.get("smiles_single_original", "")


def _render_single_analysis(export_pdf: bool) -> None:
    st.text_area(
        "原始 SMILES",
        key="smiles_single_original",
        height=82,
        placeholder="例如：CC(=O)Oc1ccccc1C(=O)O",
        on_change=_sync_single_editor,
    )
    original = str(st.session_state.get("smiles_single_original") or "")
    original_diagnostic = diagnose_smiles(original)
    _render_validation(original, original_diagnostic, "原始输入")
    if not original_diagnostic["valid"]:
        return

    st.text_area(
        "编辑或修正 SMILES（修改后实时校验与重绘）",
        key="smiles_single_editor",
        height=82,
    )
    edited = str(st.session_state.get("smiles_single_editor") or "")
    diagnostic = diagnose_smiles(edited)
    _render_validation(edited, diagnostic, "当前编辑")
    restore_col, save_col = st.columns([0.2, 0.8])
    if restore_col.button("恢复原始输入", disabled=edited == original, key="restore_single_smiles"):
        st.session_state["smiles_single_editor"] = original
        st.rerun()
    if not diagnostic["valid"]:
        return

    report = deepcopy(_cached_manual_report(edited.strip()))
    original_report = deepcopy(_cached_manual_report(original.strip()))
    if report.get("status") != "success":
        st.error(str(report.get("message") or "SMILES 分析失败。"))
        return
    if save_col.button("保存到最近分析历史", type="primary", key="save_single_smiles_history"):
        saved_report = deepcopy(report)
        saved_report["analysis_id"] = uuid4().hex
        _persist_manual_report(saved_report)
        st.success("分析记录已保存。")

    _render_structure_comparison(original_report, report, original != edited)
    _render_identity(report)
    _render_properties(report)
    _render_lipinski(report)
    _render_structure_warnings(report)
    _render_single_exports(report, export_pdf)


def _render_validation(smiles: str, diagnostic: Mapping[str, Any], label: str) -> None:
    if diagnostic.get("valid"):
        st.success(f"{label}：RDKit 校验通过。")
        for suggestion in diagnostic.get("suggestions") or []:
            st.caption(str(suggestion))
        return
    position = diagnostic.get("error_position")
    position_text = f"第 {position} 个字符" if position else "未能定位到单一字符"
    st.error(f"{label}无效：{diagnostic.get('error') or '无法解析'}（{position_text}）。")
    if position and smiles:
        marker = " " * max(0, int(position) - 1) + "^"
        st.code(f"{smiles}\n{marker}", language=None)
    suggestions = list(diagnostic.get("suggestions") or [])
    if suggestions:
        st.info("常见格式修复提示：\n\n" + "\n\n".join(f"- {item}" for item in suggestions))


def _render_structure_comparison(original_report: dict[str, Any], report: dict[str, Any], changed: bool) -> None:
    st.subheader("二维结构")
    if changed:
        columns = st.columns(2)
        _show_report_structure(columns[0], original_report, "原始输入结构")
        _show_report_structure(columns[1], report, "当前编辑结构")
        similarity = structure_similarity(report_structure_smiles(original_report), report_structure_smiles(report))
        if similarity is not None:
            st.caption(f"结构指纹相似度：{similarity:.3f}；该数值仅用于对比，不代表化学等价性。")
    else:
        _show_report_structure(st, report, "二维结构图")


def _show_report_structure(container: Any, report: Mapping[str, Any], caption: str) -> None:
    path = str(((report.get("images") or {}).get("redrawn_molecule")) or "")
    if path and Path(path).is_file():
        container.image(path, caption=caption, width=360)
    else:
        container.info("二维结构图暂不可用。")


def _render_identity(report: Mapping[str, Any]) -> None:
    identity = report.get("chemical_identity") or {}
    fields = copyable_structure_fields(report)
    st.subheader("结构标识")
    st.text_input("复制原始 SMILES", value=str(fields.get("original_smiles") or ""), key="copy_manual_original")
    columns = st.columns(2)
    columns[0].text_input(
        "复制 Canonical SMILES",
        value=str(identity.get("canonical_smiles") or fields.get("canonical_smiles") or ""),
        key="copy_manual_canonical",
    )
    columns[1].text_input(
        "复制 Standardized SMILES",
        value=str(identity.get("standardized_smiles") or report_structure_smiles(report) or ""),
        key="copy_manual_standardized",
    )
    key_columns = st.columns(2)
    key_columns[0].text_input("复制 InChIKey", value=str(fields.get("inchikey") or ""), key="copy_manual_inchikey")
    key_columns[1].text_input("分子式", value=str(identity.get("formula") or ""), key="copy_manual_formula")


def _render_properties(report: Mapping[str, Any]) -> None:
    descriptors = report.get("descriptors") or {}
    identity = report.get("chemical_identity") or {}
    st.subheader("分子性质")
    values = [
        ("分子量", descriptors.get("molecular_weight")),
        ("LogP", descriptors.get("logp")),
        ("TPSA", descriptors.get("tpsa")),
        ("HBD", descriptors.get("hbd")),
        ("HBA", descriptors.get("hba")),
        ("可旋转键", descriptors.get("rotatable_bonds")),
        ("环数", descriptors.get("ring_count")),
        ("形式电荷", identity.get("formal_charge", descriptors.get("formal_charge"))),
        ("片段数", identity.get("fragment_count", descriptors.get("fragment_count"))),
    ]
    columns = st.columns(3)
    for index, (label, value) in enumerate(values):
        columns[index % 3].metric(label, "-" if value is None else str(value))


def _render_lipinski(report: Mapping[str, Any]) -> None:
    lipinski = report.get("lipinski") or {}
    checks = list(lipinski.get("checks") or [])
    if not checks:
        return
    st.subheader("Lipinski 规则明细")
    st.caption("这里只报告规则通过项和具体超限项，不据此作确定性的药物风险或成药性结论。")
    passed = [str(item.get("message")) for item in checks if item.get("passed")]
    exceeded = [str(item.get("message")) for item in checks if not item.get("passed")]
    if passed:
        st.success("通过项：" + "；".join(passed))
    if exceeded:
        st.warning("超限项：" + "；".join(exceeded))
    else:
        st.info("没有检测到上述规则的超限项。")


def _render_structure_warnings(report: Mapping[str, Any]) -> None:
    warnings = [item for item in (report.get("structure_warnings") or []) if isinstance(item, Mapping)]
    if not warnings:
        return
    st.subheader("结构提示")
    for item in warnings:
        message = str(item.get("message") or item.get("code") or "结构需要核对")
        if item.get("severity") == "error":
            st.error(message)
        else:
            st.warning(message)


def _render_single_exports(report: Mapping[str, Any], export_pdf: bool) -> None:
    st.subheader("导出")
    smiles = str(report_structure_smiles(report) or "")
    stem = f"smiles_{str(report.get('analysis_id') or 'analysis')[:8]}"
    try:
        exports: list[tuple[str, bytes, str, str]] = [
            ("下载 SMI", (smiles + "\n").encode("utf-8"), f"{stem}.smi", "chemical/x-daylight-smiles"),
            ("下载 MOL", mol_text(report).encode("utf-8"), f"{stem}.mol", "chemical/x-mdl-molfile"),
            ("下载 SDF", sdf_text(report).encode("utf-8"), f"{stem}.sdf", "chemical/x-mdl-sdfile"),
            ("下载 CSV", csv_bytes([single_smiles_export_row(report)]), f"{stem}.csv", "text/csv"),
        ]
        columns = st.columns(5)
        for index, (label, data, filename, mime) in enumerate(exports):
            columns[index].download_button(label, data, filename, mime, key=f"single_smiles_export_{index}")
        pdf_path = OUTPUT_DIR / "smiles_exports" / f"{stem}.pdf"
        if export_pdf or not pdf_path.is_file():
            pdf_result = save_pdf(report, pdf_path)
        else:
            pdf_result = {"success": pdf_path.is_file(), "path": str(pdf_path)}
        if pdf_result.get("success") and Path(str(pdf_result.get("path"))).is_file():
            columns[4].download_button(
                "下载 PDF",
                Path(str(pdf_result["path"])).read_bytes(),
                f"{stem}.pdf",
                "application/pdf",
                key="single_smiles_pdf",
            )
        else:
            columns[4].caption("PDF 暂不可用")
    except Exception as exc:
        st.error(f"结构导出失败：{exc}")


def _render_batch_analysis() -> None:
    mode = st.radio("批量输入方式", ["批量粘贴", "CSV/SMI 文件"], horizontal=True, key="smiles_batch_mode")
    entries: list[dict[str, Any]] = []
    if mode == "批量粘贴":
        text = st.text_area(
            "每行一条 SMILES；SMILES 后可用空格或 Tab 添加名称",
            key="smiles_batch_text",
            height=180,
            placeholder="CCO ethanol\nCC(=O)O acetic_acid",
        )
        entries = parse_smiles_text(text)
    else:
        uploaded = st.file_uploader(
            "上传 CSV 或 SMI",
            type=["csv", "smi", "smiles", "txt"],
            key="smiles_batch_upload",
            help="CSV 优先读取 smiles 列；SMI 每行格式为 SMILES [名称]。",
        )
        if uploaded is not None:
            try:
                entries = parse_smiles_upload(uploaded.name, uploaded.getvalue())
            except Exception as exc:
                st.error(str(exc))
    if entries:
        duplicate_count = len(entries) - len({str(item.get("smiles") or "").strip() for item in entries})
        metrics = st.columns(3)
        metrics[0].metric("待分析", len(entries))
        metrics[1].metric("重复输入", duplicate_count)
        metrics[2].metric("上限", MAX_BATCH_SMILES)
        with st.expander("预览前 20 条输入", expanded=False):
            st.dataframe(entries[:20], width="stretch", hide_index=True)
    if st.button(
        "开始批量 RDKit 分析",
        type="primary",
        disabled=not entries or len(entries) > MAX_BATCH_SMILES,
        key="run_smiles_batch",
    ):
        with st.status("正在校验并分析 SMILES…", expanded=True) as status:
            result = _analyze_batch_entries(entries, status)
            st.session_state["smiles_batch_result"] = result
            status.update(label="批量 SMILES 分析完成", state="complete", expanded=False)
    if len(entries) > MAX_BATCH_SMILES:
        st.error(f"一次最多处理 {MAX_BATCH_SMILES} 条 SMILES，请拆分文件。")
    if st.session_state.get("smiles_batch_result"):
        _render_batch_result(st.session_state["smiles_batch_result"])


def _analyze_batch_entries(entries: list[dict[str, Any]], status: Any = None) -> dict[str, Any]:
    cache: dict[str, dict[str, Any]] = {}
    reports: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        raw = str(entry.get("smiles") or "").strip()
        diagnostic = diagnose_smiles(raw)
        if status is not None and (index == 1 or index % 25 == 0 or index == len(entries)):
            status.write(f"正在处理 {index}/{len(entries)}：{entry.get('name') or raw[:30]}")
        if not diagnostic["valid"]:
            row = report_to_smiles_row(None, entry, error=str(diagnostic.get("error") or "SMILES 无效"))
            row["错误位置"] = diagnostic.get("error_position")
            rows.append(row)
            continue
        cache_hit = raw in cache
        template = cache.get(raw)
        if template is None:
            template = deepcopy(_cached_manual_report(raw))
            cache[raw] = template
        report = deepcopy(template)
        report["analysis_id"] = uuid4().hex
        report["input"] = {
            "type": "smiles",
            "smiles": raw,
            "filename": str(entry.get("name") or f"row_{index}"),
            "source": entry.get("source"),
            "line_number": entry.get("line_number"),
        }
        reports.append(report)
        row = report_to_smiles_row(report, entry, cache_hit=cache_hit)
        row["错误位置"] = None
        rows.append(row)
    exports = smiles_batch_exports(rows, reports)
    return {
        "rows": rows,
        "reports": reports,
        "exports": exports,
        "summary": {
            "total": len(entries),
            "success": sum(row.get("状态") == "成功" for row in rows),
            "failed": sum(row.get("状态") != "成功" for row in rows),
            "cache_hits": sum(bool(row.get("缓存复用")) for row in rows),
        },
    }


def _render_batch_result(result: Mapping[str, Any]) -> None:
    summary = result.get("summary") or {}
    metrics = st.columns(4)
    metrics[0].metric("总数", summary.get("total", 0))
    metrics[1].metric("成功", summary.get("success", 0))
    metrics[2].metric("失败", summary.get("failed", 0))
    metrics[3].metric("缓存复用", summary.get("cache_hits", 0))
    rows = list(result.get("rows") or [])
    st.dataframe(rows, width="stretch", hide_index=True)
    exports = result.get("exports") or {}
    columns = st.columns(4)
    columns[0].download_button("下载批量 CSV", exports.get("csv") or b"", "smiles_batch.csv", "text/csv")
    columns[1].download_button("下载批量 SMI", exports.get("smi") or b"", "smiles_batch.smi", "chemical/x-daylight-smiles")
    columns[2].download_button("下载批量 SDF", exports.get("sdf") or b"", "smiles_batch.sdf", "chemical/x-mdl-sdfile")
    columns[3].download_button("下载失败清单", exports.get("failed_csv") or b"", "smiles_batch_failed.csv", "text/csv")
    if st.button("保存成功项到最近分析历史", key="save_smiles_batch_history"):
        for report in result.get("reports") or []:
            _persist_manual_report(dict(report))
        st.success(f"已保存 {len(result.get('reports') or [])} 条成功记录。")


def _render_recent_history() -> None:
    try:
        records = [
            item for item in AnalysisRepository().list_analyses(limit=100)
            if item.get("input_type") == "smiles"
        ][:20]
    except Exception as exc:
        st.warning(f"最近历史暂不可用：{exc}")
        return
    if not records:
        st.info("还没有保存过 SMILES 分析记录。")
        return
    options = [str(item.get("analysis_id")) for item in records]
    by_id = {str(item.get("analysis_id")): item for item in records}
    selected = st.selectbox(
        "最近记录",
        options,
        format_func=lambda value: (
            f"{by_id[value].get('created_at') or ''} · {by_id[value].get('final_smiles') or '-'} · {value[:8]}"
        ),
        key="recent_smiles_analysis",
    )
    record = by_id[selected]
    st.code(str(record.get("final_smiles") or ""), language=None)
    if st.button("载入到单条分析", key="load_recent_smiles"):
        smiles = str(record.get("final_smiles") or "")
        st.session_state["smiles_single_original"] = smiles
        st.session_state["smiles_single_editor"] = smiles
        st.rerun()
    with st.expander("历史记录详情", expanded=False):
        st.json({
            key: value
            for key, value in record.items()
            if value is not None and str(value).strip().lower() not in {"", "none"}
        })


def _persist_manual_report(report: dict[str, Any]) -> str:
    path = save_json(report, OUTPUT_DIR / "smiles_history" / f"{report['analysis_id']}.json")
    record_report(report, path)
    return path
