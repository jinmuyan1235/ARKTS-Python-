"""Auditable candidate and formal PDF reports for molecular analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape


def _clean(value: Any) -> str:
    """Return printable text while hiding empty values and literal None."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(_clean(item) for item in value if _clean(item))
    text = str(value).strip()
    return "" if not text or text.lower() == "none" else text


def _block(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


def _original_image_path(report: Mapping[str, Any]) -> Path | None:
    images = _block(report, "images")
    preprocessing = images.get("preprocessing") if isinstance(images.get("preprocessing"), Mapping) else {}
    input_data = _block(report, "input")
    for candidate in (
        preprocessing.get("uploaded_original"),
        input_data.get("path"),
        preprocessing.get("original"),
    ):
        if candidate and Path(str(candidate)).is_file():
            return Path(str(candidate)).resolve()
    return None


def save_pdf(report: Mapping[str, Any], output_path: str | Path) -> dict[str, Any]:
    """Create a candidate or formal PDF based on explicit human-review state."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            Image,
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        from reportlab.lib.utils import ImageReader
    except (ImportError, ModuleNotFoundError):
        return {
            "success": False,
            "path": None,
            "message": "未安装 reportlab，无法生成 PDF；其他分析功能不受影响。",
        }

    from src.analysis.correction import human_review_state, is_structure_confirmed

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    confirmed = is_structure_confirmed(dict(report))
    review = human_review_state(dict(report))
    report_kind = "正式报告" if confirmed else "候选报告"

    try:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            body_font = "STSong-Light"
        except Exception:
            body_font = "Helvetica"

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "MoleculeTitle",
            parent=styles["Title"],
            fontName=body_font,
            fontSize=20,
            leading=25,
            textColor=colors.HexColor("#17324D"),
            alignment=TA_LEFT,
            spaceAfter=8,
        )
        heading_style = ParagraphStyle(
            "MoleculeHeading",
            parent=styles["Heading2"],
            fontName=body_font,
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#17324D"),
            spaceBefore=10,
            spaceAfter=6,
        )
        body_style = ParagraphStyle(
            "MoleculeBody",
            parent=styles["BodyText"],
            fontName=body_font,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#253746"),
            wordWrap="CJK",
        )
        small_style = ParagraphStyle(
            "MoleculeSmall",
            parent=body_style,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#536878"),
        )
        center_style = ParagraphStyle("MoleculeCenter", parent=body_style, alignment=TA_CENTER)
        latin_body_style = ParagraphStyle("MoleculeLatinBody", parent=body_style, fontName="Helvetica")
        latin_small_style = ParagraphStyle("MoleculeLatinSmall", parent=small_style, fontName="Helvetica")

        def paragraph(value: Any, style: ParagraphStyle = body_style) -> Paragraph:
            cleaned = _clean(value)
            selected_style = style
            if cleaned.isascii():
                if style is body_style:
                    selected_style = latin_body_style
                elif style is small_style:
                    selected_style = latin_small_style
            return Paragraph(escape(cleaned), selected_style)

        def section_table(rows: list[tuple[str, Any]], widths: tuple[float, float] = (4.2, 12.3)) -> Table | None:
            visible = [(label, _clean(value)) for label, value in rows if _clean(value)]
            if not visible:
                return None
            data = [[paragraph(label), paragraph(value)] for label, value in visible]
            table = Table(data, colWidths=[widths[0] * cm, widths[1] * cm], hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF4F8")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#17324D")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD6DE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            return table

        def audit_grid(rows: list[tuple[str, Any]]) -> Table | None:
            visible = [(label, _clean(value)) for label, value in rows if _clean(value)]
            if not visible:
                return None
            data: list[list[Any]] = []
            for index in range(0, len(visible), 2):
                row: list[Any] = []
                for label, value in visible[index:index + 2]:
                    row.extend([paragraph(label), paragraph(value, small_style)])
                if len(row) == 2:
                    row.extend([paragraph(""), paragraph("")])
                data.append(row)
            table = Table(data, colWidths=[2.6 * cm, 5.65 * cm, 2.6 * cm, 5.65 * cm], hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF4F8")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EFF4F8")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD6DE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            return table

        def scaled_image(path: Path | None, max_width: float, max_height: float) -> Any:
            if path is None or not path.is_file():
                return paragraph("图片不可用", center_style)
            width, height = ImageReader(str(path)).getSize()
            scale = min(max_width / width, max_height / height)
            return Image(str(path), width=width * scale, height=height * scale)

        input_data = _block(report, "input")
        validation = _block(report, "validation")
        ocsr = _block(report, "ocsr")
        correction = _block(report, "correction")
        final = _block(report, "final")
        identity = _block(report, "chemical_identity")
        descriptors = _block(report, "descriptors")
        lipinski = _block(report, "lipinski")
        runtime = _block(report, "runtime")
        original_path = _original_image_path(report)
        images = _block(report, "images")
        structure_path = Path(str(images.get("redrawn_molecule"))) if images.get("redrawn_molecule") else None

        story: list[Any] = [
            Paragraph("分子结构图像识别与性质分析", title_style),
            paragraph(report_kind, heading_style),
        ]
        status_text = "已人工确认" if confirmed else (
            "无法确认 - 未人工确认" if review.get("status") == "unable_to_confirm" else "未人工确认"
        )
        status_color = colors.HexColor("#E6F4EA") if confirmed else colors.HexColor("#FFF0F0")
        correction_text = "已修正" if correction.get("applied") else "未修正"
        status_table = Table([
            [paragraph("确认状态"), paragraph(status_text)],
            [paragraph("修正状态"), paragraph(correction_text)],
        ], colWidths=[4.2 * cm, 12.3 * cm])
        status_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), status_color),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#4B6878")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([status_table, Spacer(1, 0.25 * cm)])
        if not confirmed:
            story.extend([
                paragraph("免责声明：本报告基于尚未人工确认的候选结构生成，仅供预览，不得作为正式结构鉴定或性质分析结论。", body_style),
                Spacer(1, 0.25 * cm),
            ])

        image_table = Table([
            [paragraph("原始图片", center_style), paragraph("最终结构" if confirmed else "候选结构", center_style)],
            [scaled_image(original_path, 7.5 * cm, 7.0 * cm), scaled_image(structure_path, 7.5 * cm, 7.0 * cm)],
        ], colWidths=[8.25 * cm, 8.25 * cm], hAlign="LEFT")
        image_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF4F8")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD6DE")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD6DE")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ]))
        story.extend([
            image_table,
            Spacer(1, 0.15 * cm),
            paragraph("说明：二维结构重绘的方向和排版可能与原图不同，结构判断应以原子、键型和连接关系为准。", small_style),
            Spacer(1, 0.2 * cm),
        ])

        current_smiles = _clean(final.get("smiles") or validation.get("standardized_smiles") or ocsr.get("smiles"))
        canonical = _clean(final.get("canonical_smiles") or validation.get("canonical_smiles"))
        predicted = _clean(ocsr.get("predicted_smiles") or ocsr.get("smiles"))
        corrected = _clean(correction.get("corrected_smiles"))
        smiles_rows: list[tuple[str, Any]] = [("最终 SMILES" if confirmed else "候选 SMILES", current_smiles)]
        if canonical and canonical != current_smiles:
            smiles_rows.append(("Canonical SMILES", canonical))
        if predicted and predicted not in {current_smiles, canonical}:
            smiles_rows.append(("模型原始预测", predicted))
        if corrected and corrected not in {current_smiles, canonical, predicted}:
            smiles_rows.append(("人工修改输入", corrected))
        story.append(Paragraph("结构标识", heading_style))
        smiles_table = section_table(smiles_rows)
        if smiles_table is not None:
            story.append(smiles_table)

        property_rows = [
            ("分子式", descriptors.get("formula") or identity.get("formula")),
            ("分子量", descriptors.get("molecular_weight")),
            ("LogP", descriptors.get("logp")),
            ("TPSA", descriptors.get("tpsa")),
            ("氢键供体 HBD", descriptors.get("hbd")),
            ("氢键受体 HBA", descriptors.get("hba")),
            ("可旋转键", descriptors.get("rotatable_bonds")),
            ("InChIKey", identity.get("inchikey")),
        ]
        violations = [_clean(item) for item in (lipinski.get("violations") or []) if _clean(item)]
        if violations:
            property_rows.append(("规则超限项", ", ".join(violations)))
        elif lipinski:
            property_rows.append(("Lipinski 检查", "未发现超限项"))
        story.append(Paragraph("核心性质" if confirmed else "候选性质预览", heading_style))
        properties_table = section_table(property_rows)
        if properties_table is not None:
            story.append(properties_table)

        audit_rows = [
            ("分析 ID", report.get("analysis_id")),
            ("报告生成时间", report.get("created_at")),
            ("输入文件", input_data.get("filename")),
            ("原图 SHA-256", input_data.get("image_sha256")),
            ("人工审核动作", review.get("action")),
            ("人工审核时间", review.get("reviewed_at")),
            ("人工修正时间", correction.get("corrected_at")),
            ("最终来源", final.get("source")),
            ("识别后端", ocsr.get("backend")),
            ("模型名称", ocsr.get("model_name")),
            ("模型版本", ocsr.get("model_version")),
            ("模型 SHA-256", ocsr.get("model_sha256")),
            (
                "推理耗时 (秒)",
                round(float(ocsr.get("inference_time_ms")) / 1000.0, 3)
                if isinstance(ocsr.get("inference_time_ms"), (int, float))
                and not isinstance(ocsr.get("inference_time_ms"), bool)
                else None,
            ),
            ("Git commit", ocsr.get("git_commit") or runtime.get("git_commit")),
            ("纠错事件数", len(report.get("correction_events") or [])),
            ("审核事件数", len(report.get("review_events") or [])),
        ]
        audit_table = audit_grid(audit_rows)
        if audit_table is not None:
            story.append(KeepTogether([Paragraph("审计信息", heading_style), audit_table]))

        story.extend([
            Spacer(1, 0.3 * cm),
            paragraph("本报告不替代实验鉴定、毒理学评价或专业决策。", small_style),
        ])

        def decorate_page(canvas: Any, document: Any) -> None:
            canvas.saveState()
            page_width, page_height = A4
            canvas.setStrokeColor(colors.HexColor("#D9E2E8"))
            canvas.line(1.7 * cm, 1.35 * cm, page_width - 1.7 * cm, 1.35 * cm)
            canvas.setFillColor(colors.HexColor("#667884"))
            canvas.setFont("Helvetica", 7.5)
            canvas.drawString(1.7 * cm, 0.9 * cm, _clean(report.get("analysis_id")))
            canvas.setFont(body_font, 7.5)
            canvas.drawRightString(page_width - 1.7 * cm, 0.9 * cm, f"第 {document.page} 页")
            if not confirmed:
                canvas.setFillColor(colors.Color(0.78, 0.12, 0.12, alpha=0.11))
                canvas.setFont(body_font, 38)
                canvas.translate(page_width / 2, page_height / 2)
                canvas.rotate(35)
                canvas.drawCentredString(0, 0, "未人工确认")
            canvas.restoreState()

        document = SimpleDocTemplate(
            str(destination),
            pagesize=A4,
            rightMargin=1.7 * cm,
            leftMargin=1.7 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.7 * cm,
            title=f"分子结构{report_kind}",
            author="Molecule Vision OCSR",
        )
        document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page)
        return {
            "success": True,
            "path": str(destination),
            "message": f"{report_kind} PDF 已生成。",
            "report_type": "formal" if confirmed else "candidate",
            "watermarked": not confirmed,
        }
    except Exception as exc:
        return {"success": False, "path": None, "message": f"PDF 导出失败：{exc}"}
