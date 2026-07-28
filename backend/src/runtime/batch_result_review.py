"""Persistent human review actions for completed batch reports."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.analysis.batch_analyzer import BatchAnalyzer, flatten_report
from src.analysis.correction import apply_smiles_correction, confirm_structure, revoke_structure_confirmation
from src.export.csv_exporter import save_csv
from src.export.structure_exporter import export_batch_structure_files
from src.utils.file_utils import ensure_directory


def apply_batch_review_actions(
    batch_result: Mapping[str, Any],
    actions: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Apply confirmation/correction actions and rebuild formal exports."""
    updated = deepcopy(dict(batch_result))
    reports = [deepcopy(report) for report in updated.get("reports") or []]
    by_id = {str(report.get("analysis_id") or ""): report for report in reports}
    destination = ensure_directory(output_dir)
    for action in actions:
        analysis_id = str(action.get("analysis_id") or "")
        if analysis_id not in by_id:
            raise ValueError(f"未找到批量结果：{analysis_id}")
        report = by_id[analysis_id]
        name = str(action.get("action") or "")
        if name == "confirm":
            candidate = confirm_structure(report)
            error = (candidate.get("human_review") or {}).get("last_error")
            if error:
                raise ValueError(f"{analysis_id} 无法确认：{error}")
        elif name == "revoke":
            candidate = revoke_structure_confirmation(report)
        elif name == "correct_smiles":
            candidate = apply_smiles_correction(report, str(action.get("smiles") or ""), destination)
            error = (candidate.get("correction") or {}).get("last_error")
            if error:
                raise ValueError(f"{analysis_id} SMILES 修正失败：{error}")
        else:
            raise ValueError(f"不支持的批量审核操作：{name}")
        index = reports.index(report)
        reports[index] = candidate
        by_id[analysis_id] = candidate

    rows = [flatten_report(report) for report in reports]
    old_summary = updated.get("summary") or {}
    summary = BatchAnalyzer._summary(rows, total=int(old_summary.get("total") or len(rows)), cancelled=bool(old_summary.get("cancelled")))
    summary["cache_hits"] = int(old_summary.get("cache_hits") or 0)
    csv_path = save_csv(rows, destination / "batch_results.csv")
    structure_exports = export_batch_structure_files(reports, destination / "structure_exports", rows)
    updated["reports"] = reports
    updated["rows"] = rows
    updated["summary"] = summary
    updated["exports"] = {**(updated.get("exports") or {}), "csv": csv_path, **structure_exports}
    return updated


def persist_batch_result(batch_result: Mapping[str, Any], result_path: str | Path) -> Path:
    """Atomically persist a UI-compatible batch result payload."""
    path = Path(result_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": batch_result.get("summary") or {},
        "rows": batch_result.get("rows") or [],
        "reports": batch_result.get("reports") or [],
        "exports": batch_result.get("exports") or {},
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path
