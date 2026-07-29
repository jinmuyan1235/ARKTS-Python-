"""Generate safe single-analysis and batch artifacts for the mobile export center."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping
import zipfile

from src.analysis.batch_analyzer import flatten_report
from src.analysis.correction import is_structure_confirmed
from src.export.csv_exporter import save_csv
from src.export.json_exporter import save_json
from src.export.pdf_exporter import save_pdf
from src.export.structure_exporter import (
    can_export_structure,
    export_batch_structure_files,
    export_structure_files,
    mol_text,
    report_structure_smiles,
)
from src.utils.file_utils import ensure_directory, safe_stem


EXPORT_FORMATS = ("csv", "json", "pdf", "smi", "mol", "sdf", "zip")
CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "pdf": "application/pdf",
    "smi": "chemical/x-daylight-smiles",
    "mol": "chemical/x-mdl-molfile",
    "sdf": "chemical/x-mdl-sdfile",
    "zip": "application/zip",
}
FORMAL_ONLY_FORMATS = {"smi", "mol", "sdf", "zip"}


def build_single_exports(report: Mapping[str, Any], output_dir: str | Path) -> dict[str, dict[str, Any]]:
    destination = ensure_directory(output_dir)
    analysis_id = str(report.get("analysis_id") or "analysis")
    stem = safe_stem(f"molecule_{analysis_id[:12]}", "molecule")
    result = _empty_export_map()

    row = flatten_report(dict(report))
    row.pop("redrawn_molecule", None)
    csv_path = Path(save_csv([row], destination / f"{stem}.csv"))
    json_path = Path(save_json(_public_payload(report), destination / f"{stem}.json"))
    result["csv"] = _available(csv_path, "csv")
    result["json"] = _available(json_path, "json")

    pdf_path = destination / f"{stem}.pdf"
    pdf_result = save_pdf(report, pdf_path)
    if pdf_result.get("success") and pdf_path.is_file():
        result["pdf"] = _available(pdf_path, "pdf")
    else:
        result["pdf"] = _unavailable(str(pdf_result.get("message") or "PDF 暂不可用。"))

    formal_allowed = is_structure_confirmed(dict(report)) and can_export_structure(report)
    if not formal_allowed:
        reason = "结构尚未人工确认，正式结构文件不可导出。"
        for export_format in FORMAL_ONLY_FORMATS:
            result[export_format] = _unavailable(reason)
        return result

    structure = export_structure_files(report, destination / "structure", prefix=stem)
    smi_path = destination / f"{stem}.smi"
    smi_path.write_text(f"{report_structure_smiles(report)}\t{analysis_id}\n", encoding="utf-8")
    result["smi"] = _available(smi_path, "smi")
    result["mol"] = _available(Path(structure["mol"]), "mol")
    result["sdf"] = _available(Path(structure["sdf"]), "sdf")
    result["zip"] = _available(Path(structure["zip"]), "zip")
    return result


def build_batch_exports(result_payload: Mapping[str, Any], output_dir: str | Path) -> dict[str, dict[str, Any]]:
    destination = ensure_directory(output_dir)
    reports = [dict(item) for item in (result_payload.get("reports") or []) if isinstance(item, Mapping)]
    rows = [dict(item) for item in (result_payload.get("rows") or []) if isinstance(item, Mapping)]
    if not rows:
        rows = [flatten_report(report) for report in reports]
    safe_rows = []
    for row in rows:
        safe_row = dict(row)
        safe_row.pop("redrawn_molecule", None)
        safe_rows.append(safe_row)

    result = _empty_export_map()
    csv_path = Path(save_csv(safe_rows, destination / "batch_results.csv"))
    json_path = Path(save_json({
        "summary": _public_payload(result_payload.get("summary") or {}),
        "results": [_public_payload(report) for report in reports],
    }, destination / "batch_results.json"))
    result["csv"] = _available(csv_path, "csv")
    result["json"] = _available(json_path, "json")

    pdf_path = destination / "batch_results.pdf"
    try:
        _save_batch_pdf(result_payload.get("summary") or {}, reports, pdf_path)
        result["pdf"] = _available(pdf_path, "pdf")
    except Exception as exc:
        result["pdf"] = _unavailable(f"批量 PDF 生成失败：{exc}")

    formal_reports = [
        report for report in reports
        if report.get("status") == "success"
        and is_structure_confirmed(report)
        and can_export_structure(report)
    ]
    if not formal_reports:
        reason = "当前批量结果中没有已人工确认的有效结构。"
        for export_format in FORMAL_ONLY_FORMATS:
            result[export_format] = _unavailable(reason)
        return result

    formal_rows = [flatten_report(report) for report in formal_reports]
    structures = export_batch_structure_files(
        formal_reports,
        destination / "confirmed_structures",
        formal_rows,
        file_prefix="batch_confirmed",
    )
    mol_zip = destination / "batch_confirmed_mol.zip"
    with zipfile.ZipFile(mol_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, report in enumerate(formal_reports, start=1):
            filename = safe_stem(str((report.get("input") or {}).get("filename") or f"structure_{index}"), f"structure_{index}")
            archive.writestr(f"{index:04d}_{filename}.mol", mol_text(report))

    result["smi"] = _available(Path(structures["merged_smi"]), "smi")
    result["mol"] = _available(mol_zip, "mol", content_type="application/zip")
    result["sdf"] = _available(Path(structures["merged_sdf"]), "sdf")
    # successful_zip contains only confirmed reports; complete_zip intentionally does not.
    result["zip"] = _available(Path(structures["successful_zip"]), "zip")
    return result


def _empty_export_map() -> dict[str, dict[str, Any]]:
    return {export_format: _unavailable("导出文件尚未生成。") for export_format in EXPORT_FORMATS}


def _available(path: Path, export_format: str, *, content_type: str | None = None) -> dict[str, Any]:
    return {
        "available": True,
        "reason": "",
        "path": str(path.resolve()),
        "filename": path.name,
        "content_type": content_type or CONTENT_TYPES[export_format],
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "path": None, "filename": None, "content_type": None}


def _public_payload(value: Any) -> Any:
    """Remove computer paths and process details before exporting JSON to a phone."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "path" or normalized.endswith("_path") or normalized in {
                "command", "pid", "stdout", "stderr", "run_dir", "output_dir", "input_dir",
                "redrawn_molecule", "source_image_path", "original_image_path",
            }:
                continue
            cleaned[str(key)] = _public_payload(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_public_payload(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return deepcopy(value)


def _save_batch_pdf(summary: Mapping[str, Any], reports: Iterable[Mapping[str, Any]], output_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    report_list = list(reports)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font = "Helvetica"
    font_candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    )
    for font_path in font_candidates:
        if not font_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("MobileExportCJK", str(font_path)))
            font = "MobileExportCJK"
            break
        except Exception:
            continue
    if font == "Helvetica":
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font = "STSong-Light"
        except Exception:
            pass
    styles = getSampleStyleSheet()
    title = ParagraphStyle("BatchTitle", parent=styles["Title"], fontName=font, fontSize=19, leading=24,
                           textColor=colors.HexColor("#17324D"), spaceAfter=10)
    body = ParagraphStyle("BatchBody", parent=styles["BodyText"], fontName=font, fontSize=8.5, leading=12,
                          textColor=colors.HexColor("#253746"), wordWrap="CJK")
    small = ParagraphStyle("BatchSmall", parent=body, fontSize=7, leading=9)
    confirmed_count = sum(is_structure_confirmed(dict(report)) for report in report_list)
    stats = [
        ("总数", summary.get("total") or len(report_list)),
        ("已完成", summary.get("completed") or len(report_list)),
        ("识别成功", summary.get("successful") or 0),
        ("人工确认", confirmed_count),
        ("失败", summary.get("failed") or 0),
        ("跳过", summary.get("skipped") or 0),
    ]
    story: list[Any] = [
        Paragraph("分子结构批量识别汇总", title),
        Paragraph("候选结果汇总；只有标记为“已确认”的结构可进入正式 SMI、MOL、SDF 和结构 ZIP。", body),
        Spacer(1, 0.25 * cm),
    ]
    stats_table = Table([[Paragraph(label, small), Paragraph(str(value), body)] for label, value in stats],
                        colWidths=[3.4 * cm, 3.0 * cm], hAlign="LEFT")
    stats_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF4F8")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD6DE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([stats_table, Spacer(1, 0.35 * cm)])

    data: list[list[Any]] = [[
        Paragraph("序号", small), Paragraph("文件", small), Paragraph("识别状态", small),
        Paragraph("确认状态", small), Paragraph("结构", small),
    ]]
    for index, report in enumerate(report_list, start=1):
        row = flatten_report(dict(report))
        smiles = str(row.get("final_smiles") or row.get("smiles") or "-")
        data.append([
            Paragraph(str(index), small),
            Paragraph(str((report.get("input") or {}).get("filename") or "-"), small),
            Paragraph(str(report.get("status") or "failed"), small),
            Paragraph("已确认" if is_structure_confirmed(dict(report)) else "未确认", small),
            Paragraph(smiles, small),
        ])
    table = Table(data, colWidths=[1.0 * cm, 4.0 * cm, 2.3 * cm, 2.3 * cm, 6.3 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD6DE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    def decorate(canvas: Any, document: Any) -> None:
        canvas.saveState()
        width, _height = A4
        canvas.setStrokeColor(colors.HexColor("#D9E2E8"))
        canvas.line(1.6 * cm, 1.25 * cm, width - 1.6 * cm, 1.25 * cm)
        canvas.setFont(font, 7.5)
        canvas.setFillColor(colors.HexColor("#667884"))
        canvas.drawRightString(width - 1.6 * cm, 0.82 * cm, f"第 {document.page} 页")
        canvas.restoreState()

    document = SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                                 topMargin=1.5 * cm, bottomMargin=1.6 * cm,
                                 title="分子结构批量识别汇总", author="Molecule Vision OCSR")
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
