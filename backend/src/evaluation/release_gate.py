"""Release acceptance gates for fixed OCSR benchmark runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from src.evaluation.metrics import is_automatic_accept


DEFAULT_THRESHOLDS = {
    "valid_smiles_rate_min": 0.95,
    "canonical_exact_match_rate_min": 0.80,
    "false_accept_rate_max": 0.05,
    "high_risk_error_review_needed_rate_min": 1.0,
    "p95_latency_ms_max": 15000.0,
    "positive_sample_count_min": 100,
    "negative_sample_count_min": 20,
    "independent_source_document_count_min": 30,
    "unique_molecule_count_min": 100,
    "unique_scaffold_count_min": 50,
    "verified_sample_rate_min": 1.0,
    "license_unclear_count_max": 0,
    "missing_image_count_max": 0,
    "checksum_error_count_max": 0,
}

DATA_SUFFICIENCY_FIELDS = (
    "total_samples",
    "positive_sample_count",
    "negative_sample_count",
    "independent_source_document_count",
    "independent_original_image_count",
    "derived_perturbation_count",
    "unique_molecule_count",
    "unique_scaffold_count",
    "verified_sample_count",
    "verified_sample_rate",
    "license_unclear_count",
    "missing_image_count",
    "checksum_error_count",
)


def _overall(metrics_payload: dict[str, Any]) -> dict[str, Any]:
    if "metrics" in metrics_payload and isinstance(metrics_payload["metrics"], dict):
        metrics_payload = metrics_payload["metrics"]
    return dict(metrics_payload.get("overall") or metrics_payload)


def evaluate_release_gates(
    metrics_payload: dict[str, Any],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate fixed release gates against benchmark metrics."""
    active = dict(DEFAULT_THRESHOLDS)
    active.update(thresholds or {})
    overall = _overall(metrics_payload)
    high_risk_error_count = int(overall.get("high_risk_error_count") or 0)
    high_risk_review_rate = (
        1.0 if high_risk_error_count == 0 else float(overall.get("high_risk_error_review_needed_rate") or 0.0)
    )
    checks = [
        _min_check(
            "valid_smiles_rate",
            overall.get("valid_smiles_rate"),
            active["valid_smiles_rate_min"],
            denominator=int(overall.get("recognition_metric_denominator") or 0),
        ),
        _min_check(
            "canonical_exact_match_rate",
            overall.get("canonical_exact_match_rate"),
            active["canonical_exact_match_rate_min"],
            denominator=int(overall.get("recognition_metric_denominator") or 0),
        ),
        _max_check(
            "false_accept_rate",
            overall.get("false_accept_rate"),
            active["false_accept_rate_max"],
            denominator=int(overall.get("rejection_metric_denominator") or 0),
        ),
        _min_check(
            "high_risk_error_review_needed_rate",
            high_risk_review_rate,
            active["high_risk_error_review_needed_rate_min"],
            denominator=high_risk_error_count,
        ),
        _max_check("p95_latency_ms", overall.get("p95_latency_ms"), active["p95_latency_ms_max"]),
        _min_check("positive_sample_count", overall.get("positive_sample_count"), active["positive_sample_count_min"]),
        _min_check("negative_sample_count", overall.get("negative_sample_count"), active["negative_sample_count_min"]),
        _min_check(
            "independent_source_document_count",
            overall.get("independent_source_document_count"),
            active["independent_source_document_count_min"],
        ),
        _min_check("unique_molecule_count", overall.get("unique_molecule_count"), active["unique_molecule_count_min"]),
        _min_check("unique_scaffold_count", overall.get("unique_scaffold_count"), active["unique_scaffold_count_min"]),
        _min_check("verified_sample_rate", overall.get("verified_sample_rate"), active["verified_sample_rate_min"]),
        _max_check("license_unclear_count", overall.get("license_unclear_count"), active["license_unclear_count_max"]),
        _max_check("missing_image_count", overall.get("missing_image_count"), active["missing_image_count_max"]),
        _max_check("checksum_error_count", overall.get("checksum_error_count"), active["checksum_error_count_max"]),
    ]
    data_sufficiency = _data_sufficiency_summary(overall, checks)
    return {
        "passed": all(check["passed"] for check in checks),
        "thresholds": active,
        "checks": checks,
        "data_sufficiency": data_sufficiency,
    }


def collect_release_error_rows(rows: Iterable[dict[str, Any]], backend: str) -> list[dict[str, Any]]:
    """Return rows that should be inspected in a release report."""
    errors: list[dict[str, Any]] = []
    for row in rows:
        expected_action = str(row.get("expected_action") or "recognize").lower()
        negative_hallucination = expected_action == "reject" and bool(row.get("rdkit_valid"))
        false_accept = negative_hallucination and is_automatic_accept(row)
        mismatch = expected_action != "reject" and not bool(row.get("canonical_exact_match"))
        if row.get("failure_reason") or negative_hallucination or mismatch:
            errors.append({
                "backend": backend,
                "sample_id": row.get("sample_id"),
                "expected_action": row.get("expected_action"),
                "category": row.get("category"),
                "source": row.get("source"),
                "image_quality": row.get("image_quality"),
                "ground_truth_smiles": row.get("ground_truth_smiles"),
                "predicted_smiles": row.get("predicted_smiles"),
                "recognition_status": row.get("recognition_status"),
                "recognition_decision": row.get("recognition_decision"),
                "canonical_exact_match": row.get("canonical_exact_match"),
                "failure_reason": row.get("failure_reason")
                or (
                    "false_accept"
                    if false_accept
                    else "negative_hallucination_review_required"
                    if negative_hallucination
                    else "mismatch"
                ),
                "inference_time_ms": row.get("inference_time_ms"),
            })
    return errors


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    """Write a CSV even when there are no rows."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = [
            "backend",
            "sample_id",
            "expected_action",
            "category",
            "source",
            "image_quality",
            "ground_truth_smiles",
            "predicted_smiles",
            "recognition_status",
            "recognition_decision",
            "canonical_exact_match",
            "failure_reason",
            "inference_time_ms",
        ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_release_report(
    path: str | Path,
    release_version: str,
    backend_payloads: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Write a Markdown release acceptance summary."""
    report_title = "OCSR Starter Acceptance Report" if "starter" in release_version.lower() else "OCSR Release Acceptance Report"
    lines = [
        f"# {report_title} {release_version}",
        "",
        "This report is generated from a fixed, reviewed acceptance manifest. Starter smoke benchmarks are not evidence of real-world OCSR accuracy.",
        "",
        "## Gate Summary",
        "",
        "| Backend | Gates | Valid SMILES | Canonical exact | False accept | Negative hallucination | High-risk review | P50 ms | P95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for backend, payload in backend_payloads.items():
        overall = _overall(payload["metrics"])
        gates = payload["gates"]
        lines.append(
            "| "
            + " | ".join([
                backend,
                "PASS" if gates["passed"] else "FAIL",
                _fmt(overall.get("valid_smiles_rate")),
                _fmt(overall.get("canonical_exact_match_rate")),
                _fmt(overall.get("false_accept_rate")),
                _fmt(overall.get("negative_hallucination_rate")),
                _fmt(
                    1.0
                    if int(overall.get("high_risk_error_count") or 0) == 0
                    else overall.get("high_risk_error_review_needed_rate")
                ),
                _fmt(overall.get("p50_latency_ms")),
                _fmt(overall.get("p95_latency_ms")),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Dataset Sufficiency",
        "",
    ])
    first_payload = next(iter(backend_payloads.values()), None)
    first_overall = _overall(first_payload["metrics"]) if first_payload else {}
    lines.extend(_dataset_sufficiency_lines(first_overall, first_payload["gates"] if first_payload else {}))
    lines.extend([
        "",
        "## Gate Details",
        "",
    ])
    for backend, payload in backend_payloads.items():
        lines.extend([f"### {backend}", ""])
        lines.append("```json")
        lines.append(json.dumps(payload["gates"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.extend([
        "## Error Rows",
        "",
        f"- Error row count: {len(errors)}",
        "- See `errors.csv` for row-level details.",
        "",
        "## Release Policy",
        "",
        "- Test/acceptance manifests must not be used for training or threshold tuning.",
        "- Starter datasets are smoke benchmarks only; they are not statistically meaningful and are not release-qualified.",
        "- Perturbations of the same source image are not independent samples.",
        "- Do not tune thresholds on this set and then report it as an independent test set.",
        "- Current backend gate failures are expected and should remain visible until a release-qualified dataset exists.",
        "- Metrics are project phase targets, not real-world accuracy claims.",
        "",
    ])
    Path(path).expanduser().resolve().write_text("\n".join(lines), encoding="utf-8")


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _min_check(name: str, value: Any, threshold: float, denominator: int | None = None) -> dict[str, Any]:
    metric = _coerce_float(value)
    passed = metric is not None and metric >= threshold
    return {
        "metric": name,
        "operator": ">=",
        "threshold": threshold,
        "value": metric,
        "denominator": denominator,
        "passed": passed,
    }


def _max_check(name: str, value: Any, threshold: float, denominator: int | None = None) -> dict[str, Any]:
    metric = _coerce_float(value)
    passed = metric is not None and metric <= threshold
    return {
        "metric": name,
        "operator": "<=",
        "threshold": threshold,
        "value": metric,
        "denominator": denominator,
        "passed": passed,
    }


def _fmt(value: Any) -> str:
    metric = _coerce_float(value)
    if metric is None:
        return "-"
    return f"{metric:.6g}"


def _data_sufficiency_summary(overall: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    data_metric_names = {
        "positive_sample_count",
        "negative_sample_count",
        "independent_source_document_count",
        "unique_molecule_count",
        "unique_scaffold_count",
        "verified_sample_rate",
        "license_unclear_count",
        "missing_image_count",
        "checksum_error_count",
    }
    data_checks = [check for check in checks if check["metric"] in data_metric_names]
    release_qualified = all(check["passed"] for check in data_checks)
    return {
        "metrics": {field: overall.get(field) for field in DATA_SUFFICIENCY_FIELDS},
        "release_qualified": release_qualified,
        "starter_dataset_only": not release_qualified,
        "not_statistically_meaningful": not release_qualified,
        "not_release_qualified": not release_qualified,
        "failed_checks": [check["metric"] for check in data_checks if not check["passed"]],
    }


def _dataset_sufficiency_lines(overall: dict[str, Any], gates: dict[str, Any]) -> list[str]:
    rows = [{field: overall.get(field) for field in DATA_SUFFICIENCY_FIELDS}]
    lines = [
        "| Metric | Value |",
        "| --- | --- |",
    ]
    labels = {
        "total_samples": "Total rows",
        "positive_sample_count": "Positive samples",
        "negative_sample_count": "Negative samples",
        "independent_source_document_count": "Independent source documents",
        "independent_original_image_count": "Independent original images",
        "derived_perturbation_count": "Derived perturbations",
        "unique_molecule_count": "Unique molecules",
        "unique_scaffold_count": "Unique scaffolds",
        "verified_sample_count": "Verified samples",
        "verified_sample_rate": "Verified sample rate",
        "license_unclear_count": "License unclear rows",
        "missing_image_count": "Missing images",
        "checksum_error_count": "Checksum errors",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {_fmt(rows[0].get(key))} |")
    sufficiency = gates.get("data_sufficiency") or {}
    if sufficiency.get("not_release_qualified"):
        lines.extend([
            "",
            "- starter dataset only",
            "- not statistically meaningful",
            "- not release-qualified",
        ])
    return lines
