"""First-round analysis for the human visual OCSR review ledger."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from src.chem.smiles_validator import validate_smiles


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required review file does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_audits(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Required review directory does not exist: {directory}")
    audits: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sample_id = str(payload.get("sample_id") or "")
        if sample_id:
            audits[sample_id] = payload
    return audits


def _human_class(audit: dict[str, Any]) -> str:
    return str(audit.get("visual_review_status") or "unreviewed")


def _machine_class(row: dict[str, Any]) -> str:
    return str(row.get("machine_category") or row.get("category") or "unknown")


def _machine_matches_human(machine: str, human: str) -> bool:
    expected = "valid_single_molecule_crop" if machine == "molecule" else machine
    return expected == human


def _has_valid_prediction(row: dict[str, Any]) -> bool:
    for backend in ("molscribe", "decimer", "ensemble"):
        if validate_smiles(str(row.get(f"{backend}_smiles") or "")).get("valid"):
            return True
    return False


def analyze_visual_review(review_dir: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    """Analyze completed visual audits without claiming full-page detector recall."""
    review_root = Path(review_dir).expanduser().resolve()
    analysis_root = Path(output_dir).expanduser().resolve() if output_dir else review_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    manifest = _read_csv(review_root / "machine_review_manifest.csv")
    detector_manifest = _read_csv(review_root / "detector_training_manifest.csv")
    audits = _load_audits(review_root / "single_reviews")
    rows_by_id = {str(row.get("sample_id") or ""): row for row in manifest}

    class_counts = Counter(_human_class(audit) for audit in audits.values())
    class_rows = [
        {"visual_review_status": status, "count": count}
        for status, count in sorted(class_counts.items())
    ]
    _write_csv(analysis_root / "visual_class_counts.csv", class_rows, ["visual_review_status", "count"])

    machine_classes = sorted({_machine_class(rows_by_id[sample_id]) for sample_id in audits if sample_id in rows_by_id})
    human_classes = sorted(class_counts)
    confusion_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for sample_id, audit in audits.items():
        if sample_id in rows_by_id:
            confusion_counts[_machine_class(rows_by_id[sample_id])][_human_class(audit)] += 1
    confusion_rows = [
        {"machine_category": machine, **{human: confusion_counts[machine][human] for human in human_classes}}
        for machine in machine_classes
    ]
    _write_csv(
        analysis_root / "machine_vs_human_confusion.csv",
        confusion_rows,
        ["machine_category", *human_classes],
    )

    rejected_rows = [
        row for row in manifest if str(row.get("verification_status") or "").startswith("rejected_")
    ]
    rejection_reasons: Counter[str] = Counter()
    for row in rejected_rows:
        reasons: list[str] = []
        for field in ("rejection_reasons", "deterministic_errors", "risk_reasons"):
            reasons.extend(str(reason) for reason in _json_list(row.get(field)) if str(reason))
        if not reasons:
            reasons.append(str(row.get("verification_status") or "machine_rejected"))
        rejection_reasons.update(set(reasons))
    rejection_rows = [
        {"rejection_reason": reason, "sample_count": count}
        for reason, count in sorted(rejection_reasons.items())
    ]
    _write_csv(
        analysis_root / "machine_rejection_reasons.csv",
        rejection_rows,
        ["rejection_reason", "sample_count"],
    )

    document_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in manifest:
        document = str(row.get("source_document") or row.get("source_id") or "unknown")
        page = str(row.get("source_page_path") or "")
        document_groups[(document, page)].append(row)
    document_rows: list[dict[str, Any]] = []
    for (document, page), rows in sorted(document_groups.items()):
        reviewed = [(row, audits[str(row.get("sample_id") or "")]) for row in rows if str(row.get("sample_id") or "") in audits]
        misclassified = sum(not _machine_matches_human(_machine_class(row), _human_class(audit)) for row, audit in reviewed)
        false_positives = sum(
            _machine_class(row) == "molecule" and _human_class(audit) != "valid_single_molecule_crop"
            for row, audit in reviewed
        )
        document_rows.append({
            "source_document": document, "source_page_path": page, "candidate_count": len(rows),
            "reviewed_count": len(reviewed), "misclassified_count": misclassified,
            "machine_molecule_false_positive_count": false_positives,
        })
    _write_csv(
        analysis_root / "per_document_metrics.csv",
        document_rows,
        [
            "source_document", "source_page_path", "candidate_count", "reviewed_count",
            "misclassified_count", "machine_molecule_false_positive_count",
        ],
    )

    bbox_reviewed = [audit for audit in audits.values() if audit.get("bbox_before") or audit.get("bbox_after")]
    bbox_modified = sum(
        list(audit.get("bbox_before") or []) != list(audit.get("bbox_after") or [])
        for audit in bbox_reviewed
    )
    bbox_summary = {
        "reviewed_with_bbox": len(bbox_reviewed),
        "bbox_modified": bbox_modified,
        "bbox_modification_rate": bbox_modified / len(bbox_reviewed) if bbox_reviewed else None,
    }
    (analysis_root / "bbox_correction_summary.json").write_text(
        json.dumps(bbox_summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    reviewed_machine_molecules = [
        (row, audits[sample_id]) for sample_id, row in rows_by_id.items()
        if sample_id in audits and _machine_class(row) == "molecule"
    ]
    valid_machine_molecules = sum(
        _human_class(audit) == "valid_single_molecule_crop" for _, audit in reviewed_machine_molecules
    )
    false_positive_counts = Counter(
        _human_class(audit) for _, audit in reviewed_machine_molecules
        if _human_class(audit) != "valid_single_molecule_crop"
    )
    negative_valid_smiles = sum(
        _machine_class(row) != "molecule" and _has_valid_prediction(row) for row in manifest
    )
    molecule_validity_rate = (
        valid_machine_molecules / len(reviewed_machine_molecules) if reviewed_machine_molecules else None
    )
    report_lines = [
        "# Visual Review Analysis",
        "",
        "## Scope limitation",
        "",
        "This report analyzes candidate crops that reached the review ledger. It **cannot estimate complete-page detection recall**, because pages and regions missed by the detector are absent from these inputs.",
        "",
        "## Summary",
        "",
        f"- Machine-manifest candidates: {len(manifest)}",
        f"- Completed human visual audits: {len(audits)}",
        f"- Detector-training rows: {len(detector_manifest)}",
        f"- Reviewed machine molecule candidates: {len(reviewed_machine_molecules)}",
        f"- Visually valid machine molecule candidates: {valid_machine_molecules}",
        f"- Machine molecule visual validity rate: {molecule_validity_rate if molecule_validity_rate is not None else 'n/a'}",
        f"- Bbox modification rate: {bbox_summary['bbox_modification_rate'] if bbox_summary['bbox_modification_rate'] is not None else 'n/a'}",
        f"- Machine-rejected candidates: {len(rejected_rows)}",
        f"- Negative machine candidates producing at least one valid SMILES: {negative_valid_smiles}",
        "",
        "## Human visual classes",
        "",
        *[f"- {status}: {count}" for status, count in sorted(class_counts.items())],
        "",
        "## Machine molecule false positives by human class",
        "",
        *([f"- {status}: {count}" for status, count in sorted(false_positive_counts.items())] or ["- None"]),
        "",
        "## Machine rejection reasons",
        "",
        *([f"- {reason}: {count}" for reason, count in sorted(rejection_reasons.items())] or ["- None recorded"]),
        "",
        "See `machine_vs_human_confusion.csv` for the full machine-to-human confusion matrix and `per_document_metrics.csv` for candidate and misclassification counts by source document and page.",
        "",
    ]
    report_path = analysis_root / "visual_review_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    return {
        "analysis_dir": str(analysis_root), "manifest_candidates": len(manifest),
        "reviewed_samples": len(audits), "detector_training_samples": len(detector_manifest),
        "machine_molecule_visual_validity_rate": molecule_validity_rate,
        "negative_candidates_with_valid_smiles": negative_valid_smiles,
        "report": str(report_path),
    }
