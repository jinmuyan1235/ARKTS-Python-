"""Parsing and export helpers for interactive and batch SMILES analysis."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.export.structure_exporter import copyable_structure_fields, report_structure_smiles, sdf_text


SMILES_COLUMN_NAMES = {"smiles", "canonical_smiles", "structure", "molecule", "分子", "结构"}
NAME_COLUMN_NAMES = {"name", "id", "identifier", "title", "名称", "编号"}


def parse_smiles_text(text: str, source: str = "paste") -> list[dict[str, Any]]:
    """Parse one-SMILES-per-line text, including common SMI name suffixes."""
    entries: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        entries.append({
            "source": source,
            "line_number": line_number,
            "smiles": parts[0].strip(),
            "name": parts[1].strip() if len(parts) > 1 else f"row_{line_number}",
            "raw_line": raw_line,
        })
    return entries


def parse_smiles_upload(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Parse CSV, SMI, or text upload bytes into stable entry dictionaries."""
    suffix = Path(str(filename or "upload.smi")).suffix.lower()
    text = _decode_text(content)
    if suffix in {".smi", ".smiles", ".txt"}:
        return parse_smiles_text(text, source=filename)
    if suffix != ".csv":
        raise ValueError("仅支持 CSV、SMI、SMILES 或 TXT 文件。")
    return _parse_csv_text(text, filename)


def report_to_smiles_row(
    report: Mapping[str, Any] | None,
    entry: Mapping[str, Any],
    *,
    error: str | None = None,
    cache_hit: bool = False,
) -> dict[str, Any]:
    """Flatten one manual report into the SMILES page batch schema."""
    report = report or {}
    identity = _block(report, "chemical_identity")
    descriptors = _block(report, "descriptors")
    standardization = _block(report, "standardization")
    return {
        "行号": entry.get("line_number"),
        "名称": entry.get("name") or "",
        "原始 SMILES": entry.get("smiles") or "",
        "Canonical SMILES": identity.get("canonical_smiles") or "",
        "Standardized SMILES": identity.get("standardized_smiles") or "",
        "InChIKey": identity.get("inchikey") or "",
        "分子式": identity.get("formula") or descriptors.get("formula") or "",
        "分子量": descriptors.get("molecular_weight"),
        "LogP": descriptors.get("logp"),
        "TPSA": descriptors.get("tpsa"),
        "HBD": descriptors.get("hbd"),
        "HBA": descriptors.get("hba"),
        "可旋转键": descriptors.get("rotatable_bonds"),
        "环数": descriptors.get("ring_count"),
        "电荷": identity.get("formal_charge", descriptors.get("formal_charge")),
        "片段数": identity.get("fragment_count", descriptors.get("fragment_count")),
        "标准化配置": standardization.get("profile") or "",
        "状态": "成功" if report.get("status") == "success" and not error else "失败",
        "失败原因": error or (report.get("message") if report.get("status") != "success" else "") or "",
        "缓存复用": bool(cache_hit),
        "分析 ID": report.get("analysis_id") or "",
    }


def smiles_batch_exports(
    rows: Iterable[Mapping[str, Any]],
    reports: Iterable[Mapping[str, Any]],
) -> dict[str, bytes]:
    """Build CSV, SMI, SDF, and failure CSV bytes for a manual batch."""
    row_list = [dict(row) for row in rows]
    report_list = [dict(report) for report in reports]
    valid_reports = [report for report in report_list if report.get("status") == "success" and report_structure_smiles(report)]
    csv_bytes = _csv_bytes(row_list)
    failure_rows = [row for row in row_list if row.get("状态") != "成功"]
    failure_bytes = _csv_bytes(failure_rows, columns=list(row_list[0]) if row_list else None)
    smi_lines: list[str] = []
    sdf_records: list[str] = []
    for index, report in enumerate(valid_reports, start=1):
        smiles = report_structure_smiles(report)
        if not smiles:
            continue
        input_data = _block(report, "input")
        name = str(input_data.get("filename") or input_data.get("name") or report.get("analysis_id") or f"row_{index}")
        smi_lines.append(f"{smiles}\t{name}")
        sdf_records.append(sdf_text(report))
    return {
        "csv": csv_bytes,
        "smi": ("\n".join(smi_lines) + ("\n" if smi_lines else "")).encode("utf-8"),
        "sdf": "".join(sdf_records).encode("utf-8"),
        "failed_csv": failure_bytes,
    }


def single_smiles_export_row(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return one complete CSV row while accepting legacy manual reports."""
    identity = _block(report, "chemical_identity")
    descriptors = _block(report, "descriptors")
    fields = copyable_structure_fields(report)
    return {
        "Original SMILES": fields.get("original_smiles") or "",
        "Canonical SMILES": fields.get("canonical_smiles") or "",
        "Standardized SMILES": identity.get("standardized_smiles") or report_structure_smiles(report) or "",
        "InChIKey": fields.get("inchikey") or "",
        "Molecular Formula": identity.get("formula") or descriptors.get("formula") or "",
        "Molecular Weight": descriptors.get("molecular_weight"),
        "LogP": descriptors.get("logp"),
        "TPSA": descriptors.get("tpsa"),
        "HBD": descriptors.get("hbd"),
        "HBA": descriptors.get("hba"),
        "Rotatable Bonds": descriptors.get("rotatable_bonds"),
        "Ring Count": descriptors.get("ring_count"),
        "Formal Charge": identity.get("formal_charge", descriptors.get("formal_charge")),
        "Fragment Count": identity.get("fragment_count", descriptors.get("fragment_count")),
    }


def csv_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Public UTF-8-BOM CSV serializer for download buttons."""
    return _csv_bytes([dict(row) for row in rows])


def _parse_csv_text(text: str, filename: str) -> list[dict[str, Any]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    normalized_header = [str(value).strip().lower().replace(" ", "_") for value in rows[0]]
    smiles_index = next((index for index, value in enumerate(normalized_header) if value in SMILES_COLUMN_NAMES), None)
    name_index = next((index for index, value in enumerate(normalized_header) if value in NAME_COLUMN_NAMES), None)
    start = 1 if smiles_index is not None else 0
    if smiles_index is None:
        smiles_index = 0
    entries: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[start:], start=start + 1):
        if smiles_index >= len(row) or not str(row[smiles_index]).strip():
            continue
        name = (
            str(row[name_index]).strip()
            if name_index is not None and name_index < len(row) and str(row[name_index]).strip()
            else f"row_{row_index}"
        )
        entries.append({
            "source": filename,
            "line_number": row_index,
            "smiles": str(row[smiles_index]).strip(),
            "name": name,
            "raw_line": dialect.delimiter.join(row),
        })
    return entries


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return bytes(content).decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法解码文件，请使用 UTF-8 或 GB18030 文本编码。")


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str] | None = None) -> bytes:
    output = io.StringIO(newline="")
    fieldnames = columns or (list(rows[0]) if rows else ["状态", "失败原因"])
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _block(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}
