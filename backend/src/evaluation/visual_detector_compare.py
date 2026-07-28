"""Compare two visual-detector regression runs without overstating development gains."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


MAJOR_NEGATIVE_CLASSES = ("reaction", "text", "table")
MACRO_F1_DROP_TOLERANCE = 0.02
UNCERTAIN_RATE_INCREASE_LIMIT = 0.05
UNCERTAIN_RATE_ABSOLUTE_LIMIT = 0.15


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Comparison input is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_documents(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row["source_document"]): row for row in csv.DictReader(handle)}


def compare_visual_detector_runs(
    baseline_dir: str | Path,
    candidate_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    baseline_root = Path(baseline_dir).expanduser().resolve()
    candidate_root = Path(candidate_dir).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    baseline = _read_json(baseline_root / "metrics.json")
    candidate = _read_json(candidate_root / "metrics.json")

    def metric(source: dict[str, Any], name: str) -> float:
        return float(source["molecule_vs_non_molecule"][name])

    tracked = {
        "molecule_precision": (metric(baseline, "precision"), metric(candidate, "precision")),
        "molecule_recall": (metric(baseline, "recall"), metric(candidate, "recall")),
        "molecule_f1": (metric(baseline, "f1"), metric(candidate, "f1")),
        "macro_f1": (float(baseline["multiclass"]["macro_f1"]), float(candidate["multiclass"]["macro_f1"])),
        "false_positive_rate": (metric(baseline, "false_positive_rate"), metric(candidate, "false_positive_rate")),
        "molecule_candidate_purity": (
            float(baseline["molecule_candidate_purity"]), float(candidate["molecule_candidate_purity"]),
        ),
        "uncertain_prediction_rate": (
            float(baseline["uncertain_prediction_rate"]), float(candidate["uncertain_prediction_rate"]),
        ),
    }
    metric_comparison = {
        name: {"baseline": values[0], "candidate": values[1], "delta": values[1] - values[0]}
        for name, values in tracked.items()
    }
    class_recalls: dict[str, Any] = {}
    negative_recalls_preserved = True
    for label in MAJOR_NEGATIVE_CLASSES:
        baseline_row = baseline["multiclass"]["per_class"].get(label)
        candidate_row = candidate["multiclass"]["per_class"].get(label)
        if not baseline_row or not candidate_row or int(baseline_row.get("support", 0)) == 0:
            class_recalls[label] = {"status": "not_evaluable"}
            continue
        baseline_recall = float(baseline_row["recall"])
        candidate_recall = float(candidate_row["recall"])
        preserved = candidate_recall >= baseline_recall
        negative_recalls_preserved = negative_recalls_preserved and preserved
        class_recalls[label] = {
            "baseline": baseline_recall, "candidate": candidate_recall,
            "delta": candidate_recall - baseline_recall, "preserved": preserved,
        }
    baseline_documents = _read_documents(baseline_root / "per_document_metrics.csv")
    candidate_documents = _read_documents(candidate_root / "per_document_metrics.csv")
    document_changes = []
    for document in sorted(set(baseline_documents) | set(candidate_documents)):
        old = baseline_documents.get(document, {})
        new = candidate_documents.get(document, {})
        document_changes.append({
            "source_document": document,
            "baseline_error_count": int(old.get("error_count", 0) or 0),
            "candidate_error_count": int(new.get("error_count", 0) or 0),
            "error_delta": int(new.get("error_count", 0) or 0) - int(old.get("error_count", 0) or 0),
            "baseline_molecule_f1": float(old.get("molecule_f1", 0.0) or 0.0),
            "candidate_molecule_f1": float(new.get("molecule_f1", 0.0) or 0.0),
        })
    precision_improved = tracked["molecule_precision"][1] > tracked["molecule_precision"][0]
    macro_preserved = tracked["macro_f1"][1] >= tracked["macro_f1"][0] - MACRO_F1_DROP_TOLERANCE
    uncertain_ok = (
        tracked["uncertain_prediction_rate"][1] <= UNCERTAIN_RATE_ABSOLUTE_LIMIT
        and tracked["uncertain_prediction_rate"][1] <= tracked["uncertain_prediction_rate"][0] + UNCERTAIN_RATE_INCREASE_LIMIT
    )
    criteria = {
        "molecule_precision_improved": precision_improved,
        "macro_f1_not_materially_lower": macro_preserved,
        "major_negative_recalls_preserved": negative_recalls_preserved,
        "uncertain_rate_not_gamed": uncertain_ok,
    }
    comparison = {
        "baseline_dir": str(baseline_root), "candidate_dir": str(candidate_root),
        "development_only": bool(baseline.get("development_only") or candidate.get("development_only")),
        "metrics": metric_comparison, "major_negative_class_recalls": class_recalls,
        "error_counts": {
            "baseline": baseline.get("error_counts", {}), "candidate": candidate.get("error_counts", {}),
        },
        "per_document_changes": document_changes, "acceptance_criteria": criteria,
        "development_set_improved": all(criteria.values()),
        "generalization_claim_allowed": not bool(baseline.get("development_only") or candidate.get("development_only")),
    }
    (output_root / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Visual Detector Run Comparison", "",
        f"Development-set acceptance criteria passed: **{comparison['development_set_improved']}**", "",
        "## Metrics", "",
        "| Metric | Baseline | Candidate | Delta |", "|---|---:|---:|---:|",
    ]
    for name, values in metric_comparison.items():
        lines.append(f"| {name} | {values['baseline']:.6f} | {values['candidate']:.6f} | {values['delta']:+.6f} |")
    lines.extend(["", "## Guardrails", ""])
    lines.extend(f"- {name}: {passed}" for name, passed in criteria.items())
    lines.extend(["", "## Per-document error changes", ""])
    lines.extend(
        f"- {row['source_document']}: {row['baseline_error_count']} -> {row['candidate_error_count']} ({row['error_delta']:+d})"
        for row in document_changes
    )
    if comparison["development_only"]:
        lines.extend([
            "", "## Generalization limitation", "",
            "Only development-set results are compared. A frozen holdout has not been evaluated, so improved generalization is not claimed.",
        ])
    (output_root / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return comparison
