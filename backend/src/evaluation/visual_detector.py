"""Offline regression evaluation for the shared visual candidate screen."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from src.documents.candidate_screening import get_screening_config, screen_region_candidate


KNOWN_INITIAL_TYPES = (
    "multiple_molecules", "invalid_crop", "reaction", "molecule", "figure", "table", "text", "logo", "blank",
)
DATASET_ROLES = {"development", "holdout"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Visual detector manifest does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _truth_class(row: dict[str, str]) -> str:
    value = str(row.get("visual_review_status") or "uncertain_visual")
    if value == "valid_single_molecule_crop":
        return "molecule"
    if value in {"uncertain_visual", "missing_source_file"}:
        return "uncertain"
    return value


def _initial_class(row: dict[str, str]) -> str:
    explicit = str(row.get("original_machine_category") or row.get("machine_category") or "")
    if explicit:
        return explicit
    sample_id = str(row.get("sample_id") or "")
    for label in KNOWN_INITIAL_TYPES:
        if re.search(rf"_{re.escape(label)}_[0-9a-f]+$", sample_id):
            return label
    return "uncertain"


def _resolve_image(row: dict[str, str], manifest_path: Path) -> Path:
    for field in ("image_path", "resolved_image_path"):
        raw = str(row.get(field) or "")
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        root = Path(str(row.get("dataset_root") or "")).expanduser()
        rooted = root / candidate
        if rooted.is_file():
            return rooted.resolve()
        relative = manifest_path.parent / candidate
        if relative.is_file():
            return relative.resolve()
    raise FileNotFoundError(f"Candidate image is missing for sample {row.get('sample_id')}")


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _resolve_dataset_role(manifest_path: Path, explicit_role: str | None) -> tuple[str, str]:
    """Resolve role without using directory-name heuristics."""
    if explicit_role is not None:
        if explicit_role not in DATASET_ROLES:
            raise ValueError(f"Unsupported dataset role: {explicit_role}")
        return explicit_role, "command_line"
    summary_path = manifest_path.parent / "dataset_summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Unable to read dataset summary: {summary_path}") from exc
        role = str(summary.get("dataset_role") or "").strip().lower()
        if role:
            if role not in DATASET_ROLES:
                raise ValueError(f"Unsupported dataset role in {summary_path}: {role}")
            return role, "dataset_summary"
    return "development", "default"


def _classification_metrics(truths: list[str], predictions: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    labels = sorted(set(truths) | set(predictions))
    confusion = Counter(zip(truths, predictions))
    per_class: list[dict[str, Any]] = []
    for label in labels:
        tp = confusion[(label, label)]
        fp = sum(confusion[(truth, label)] for truth in labels if truth != label)
        fn = sum(confusion[(label, predicted)] for predicted in labels if predicted != label)
        support = sum(truth == label for truth in truths)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        per_class.append({
            "class": label, "precision": precision, "recall": recall, "f1": f1,
            "support": support, "predicted_count": sum(predicted == label for predicted in predictions),
        })
    macro_f1 = _safe_div(sum(row["f1"] for row in per_class), len(per_class))
    weighted_f1 = _safe_div(sum(row["f1"] * row["support"] for row in per_class), len(truths))
    confusion_rows = [
        {"actual_class": truth, **{predicted: confusion[(truth, predicted)] for predicted in labels}}
        for truth in labels
    ]
    return {"labels": labels, "macro_f1": macro_f1, "weighted_f1": weighted_f1}, per_class, confusion_rows


def _binary_metrics(truths: list[str], predictions: list[str]) -> dict[str, float | int]:
    truth_positive = [truth == "molecule" for truth in truths]
    predicted_positive = [prediction == "molecule" for prediction in predictions]
    tp = sum(truth and predicted for truth, predicted in zip(truth_positive, predicted_positive))
    fp = sum(not truth and predicted for truth, predicted in zip(truth_positive, predicted_positive))
    fn = sum(truth and not predicted for truth, predicted in zip(truth_positive, predicted_positive))
    tn = sum(not truth and not predicted for truth, predicted in zip(truth_positive, predicted_positive))
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    return {
        "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
        "precision": precision, "recall": recall,
        "f1": _safe_div(2 * precision * recall, precision + recall),
        "false_positive_rate": _safe_div(fp, fp + tn),
        "negative_rejection_rate": _safe_div(tn, tn + fp),
        "molecule_candidate_purity": precision,
    }


def _screen_row(row: dict[str, str], manifest_path: Path, config_name: str) -> dict[str, Any]:
    image_path = _resolve_image(row, manifest_path)
    initial = _initial_class(row)
    if config_name == "baseline":
        return {
            "sample_id": row.get("sample_id", ""), "source_document": row.get("source_document", ""),
            "source_page_path": row.get("source_page_path", ""), "image_path": str(image_path),
            "truth_class": _truth_class(row), "initial_region_type": initial,
            "predicted_class": initial, "molecule_candidate": initial == "molecule",
            "screening_score": "", "reason_codes": json.dumps(["baseline_original_category"]),
            "config": config_name,
            "diagnostics": json.dumps({"baseline": "frozen original detector category"}, sort_keys=True),
        }
    image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to decode candidate image: {image_path}")
    height, width = image.shape[:2]
    result = screen_region_candidate(
        image, (0, 0, width, height), initial, None, config=get_screening_config(config_name),
    )
    return {
        "sample_id": row.get("sample_id", ""), "source_document": row.get("source_document", ""),
        "source_page_path": row.get("source_page_path", ""), "image_path": str(image_path),
        "truth_class": _truth_class(row), "initial_region_type": initial,
        "predicted_class": result.recommended_region_type,
        "molecule_candidate": result.molecule_candidate,
        "screening_score": result.screening_score,
        "reason_codes": json.dumps(result.reason_codes, ensure_ascii=False),
        "config": config_name, "diagnostics": json.dumps(result.diagnostics, ensure_ascii=False, sort_keys=True),
    }


def evaluate_visual_detector(
    manifest: str | Path,
    output: str | Path,
    *,
    config_name: str = "baseline",
    dataset_role: str | None = None,
) -> dict[str, Any]:
    """Evaluate candidate classification only; full-page detection recall is out of scope."""
    manifest_path = Path(manifest).expanduser().resolve()
    output_dir = Path(output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(manifest_path)
    predictions: list[dict[str, Any]] = []
    for row in rows:
        try:
            predictions.append(_screen_row(row, manifest_path, config_name))
        except Exception as exc:
            predictions.append({
                "sample_id": row.get("sample_id", ""), "source_document": row.get("source_document", ""),
                "source_page_path": row.get("source_page_path", ""), "image_path": row.get("image_path", ""),
                "truth_class": _truth_class(row), "initial_region_type": _initial_class(row),
                "predicted_class": "uncertain", "molecule_candidate": False, "screening_score": 0.0,
                "reason_codes": json.dumps(["uncertain"]), "config": config_name,
                "diagnostics": json.dumps({"error": str(exc)}, ensure_ascii=False),
            })
    fields = [
        "sample_id", "source_document", "source_page_path", "image_path", "truth_class",
        "initial_region_type", "predicted_class", "molecule_candidate", "screening_score",
        "reason_codes", "config", "diagnostics",
    ]
    _write_csv(output_dir / "predictions.csv", predictions, fields)
    truths = [str(row["truth_class"]) for row in predictions]
    predicted = [str(row["predicted_class"]) for row in predictions]
    binary = _binary_metrics(truths, predicted)
    multiclass, per_class, confusion = _classification_metrics(truths, predicted)
    _write_csv(
        output_dir / "per_class_metrics.csv", per_class,
        ["class", "precision", "recall", "f1", "support", "predicted_count"],
    )
    _write_csv(output_dir / "confusion_matrix.csv", confusion, ["actual_class", *multiclass["labels"]])
    errors = [row for row in predictions if row["truth_class"] != row["predicted_class"]]
    _write_csv(output_dir / "errors.csv", errors, fields)
    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        by_document[str(row["source_document"] or "unknown")].append(row)
    document_rows: list[dict[str, Any]] = []
    for document, items in sorted(by_document.items()):
        item_truths = [str(item["truth_class"]) for item in items]
        item_predictions = [str(item["predicted_class"]) for item in items]
        item_binary = _binary_metrics(item_truths, item_predictions)
        document_rows.append({
            "source_document": document, "sample_count": len(items),
            "error_count": sum(a != b for a, b in zip(item_truths, item_predictions)),
            "accuracy": _safe_div(sum(a == b for a, b in zip(item_truths, item_predictions)), len(items)),
            "molecule_precision": item_binary["precision"], "molecule_recall": item_binary["recall"],
            "molecule_f1": item_binary["f1"], "false_positive_rate": item_binary["false_positive_rate"],
        })
    _write_csv(
        output_dir / "per_document_metrics.csv", document_rows,
        [
            "source_document", "sample_count", "error_count", "accuracy", "molecule_precision",
            "molecule_recall", "molecule_f1", "false_positive_rate",
        ],
    )
    resolved_role, role_source = _resolve_dataset_role(manifest_path, dataset_role)
    error_counts = Counter(f"{row['predicted_class']}->{row['truth_class']}" for row in errors)
    metrics = {
        "manifest": str(manifest_path), "output": str(output_dir), "config": config_name,
        "dataset_role": resolved_role, "dataset_role_source": role_source,
        "development_only": resolved_role == "development",
        "sample_count": len(predictions), "molecule_vs_non_molecule": binary,
        "multiclass": {**multiclass, "per_class": {row["class"]: row for row in per_class}},
        "molecule_candidate_purity": binary["molecule_candidate_purity"],
        "uncertain_prediction_count": sum(value == "uncertain" for value in predicted),
        "uncertain_prediction_rate": _safe_div(sum(value == "uncertain" for value in predicted), len(predicted)),
        "error_counts": dict(sorted(error_counts.items())),
        "scope_limitation": (
            "Candidate-screening metrics measure molecule/non-molecule classification only for existing crops already "
            "proposed by the baseline detector. Candidate-formation metrics are not completely measurable from this "
            "manifest: it cannot measure complete-page molecule detection recall, the number of molecules missed on a "
            "page, bbox proposal recall, or performance on regions never proposed by the baseline detector."
        ),
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    report = [
        f"# Visual Detector Evaluation ({config_name})", "",
        f"Dataset role: **{resolved_role}** (source: `{role_source}`)", "",
        "## A. Candidate-screening metrics", "",
        "These metrics evaluate molecule/non-molecule classification for existing candidate crops.", "",
        "## B. Candidate-formation metrics", "",
        metrics["scope_limitation"], "",
    ]
    if resolved_role == "development":
        report.extend([
            "Only development-set evaluation has been completed. No holdout result is available, so this report does not claim improved generalization.", "",
        ])
    report.extend([
        "## Molecule vs non-molecule", "",
        f"- Precision: {binary['precision']:.6f}", f"- Recall: {binary['recall']:.6f}",
        f"- F1: {binary['f1']:.6f}", f"- False-positive rate: {binary['false_positive_rate']:.6f}",
        f"- Negative rejection rate: {binary['negative_rejection_rate']:.6f}",
        f"- Molecule candidate purity: {binary['molecule_candidate_purity']:.6f}", "",
        "## Multiclass", "", f"- Macro F1: {multiclass['macro_f1']:.6f}",
        f"- Weighted F1: {multiclass['weighted_f1']:.6f}",
        f"- Uncertain prediction rate: {metrics['uncertain_prediction_rate']:.6f}", "",
        "See `per_class_metrics.csv`, `confusion_matrix.csv`, `per_document_metrics.csv`, and `errors.csv` for details.", "",
    ])
    (output_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    return metrics
