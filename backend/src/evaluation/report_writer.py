"""Write OCSR benchmark reports, tabular exports and simple charts."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def create_run_directory(output_root: str | Path, backend: str, timestamp: str | None = None) -> Path:
    """Create a unique benchmark run directory without overwriting history."""
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{stamp}_{backend}"
    candidate = root / base_name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    (candidate / "charts").mkdir()
    return candidate


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _bar_chart(path: Path, title: str, values: dict[str, int | float]) -> None:
    width, height = 760, 420
    margin = 64
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((margin, 20), title, fill="#222222")
    if not values:
        draw.text((margin, 80), "No data", fill="#666666")
        image.save(path)
        return
    labels = list(values.keys())
    numbers = [0.0 if value is None else float(value) for value in values.values()]
    max_value = max(numbers + [1.0])
    bar_width = max(24, min(90, (width - margin * 2) // max(len(labels) * 2, 1)))
    gap = max(18, (width - margin * 2 - bar_width * len(labels)) // max(len(labels), 1))
    baseline = height - margin
    draw.line((margin, baseline, width - margin // 2, baseline), fill="#333333", width=2)
    draw.line((margin, margin, margin, baseline), fill="#333333", width=2)
    for index, (label, number) in enumerate(zip(labels, numbers)):
        x0 = margin + index * (bar_width + gap) + gap // 2
        bar_height = int((baseline - margin) * (number / max_value))
        y0 = baseline - bar_height
        draw.rectangle((x0, y0, x0 + bar_width, baseline), fill="#457b9d")
        draw.text((x0, max(y0 - 18, 44)), f"{number:g}", fill="#222222")
        draw.text((x0, baseline + 10), str(label)[:18], fill="#222222")
    image.save(path)


def _histogram(path: Path, title: str, values: list[float], bins: int = 10) -> None:
    if not values:
        _bar_chart(path, title, {})
        return
    minimum, maximum = min(values), max(values)
    if minimum == maximum:
        bucket_counts = {f"{minimum:g}": len(values)}
    else:
        step = (maximum - minimum) / bins
        counts = [0] * bins
        for value in values:
            index = min(bins - 1, int((value - minimum) / step))
            counts[index] += 1
        bucket_counts = {
            f"{minimum + index * step:.2f}-{minimum + (index + 1) * step:.2f}": count
            for index, count in enumerate(counts)
        }
    _bar_chart(path, title, bucket_counts)


def _write_charts(run_dir: Path, rows: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, str]:
    chart_dir = run_dir / "charts"
    outputs: dict[str, str] = {}
    chart_jobs = {
        "success_failure_invalid.png": lambda path: _bar_chart(
            path,
            "Recognition / RDKit counts",
            {
                "success": metrics["overall"]["recognition_success_count"],
                "failed": metrics["overall"]["failed_count"],
                "invalid_smiles": metrics["overall"]["recognition_success_count"]
                - metrics["overall"]["rdkit_valid_count"],
            },
        ),
        "category_accuracy.png": lambda path: _bar_chart(
            path,
            "Canonical exact match rate by category",
            {
                category: group["canonical_exact_match_rate"]
                for category, group in metrics.get("groups", {}).get("category", {}).items()
            },
        ),
        "image_quality_accuracy.png": lambda path: _bar_chart(
            path,
            "Canonical exact match rate by image quality",
            {
                quality: group["canonical_exact_match_rate"]
                for quality, group in metrics.get("groups", {}).get("image_quality", {}).items()
            },
        ),
        "similarity_distribution.png": lambda path: _histogram(
            path,
            "Tanimoto similarity distribution",
            [float(row["tanimoto_similarity"]) for row in rows if row.get("tanimoto_similarity") is not None],
        ),
        "latency_distribution.png": lambda path: _histogram(
            path,
            "Inference latency distribution (ms)",
            [float(row["inference_time_ms"]) for row in rows if row.get("inference_time_ms") is not None],
        ),
        "failure_reason_distribution.png": lambda path: _bar_chart(
            path,
            "Failure reasons",
            metrics["overall"].get("failure_reason_distribution", {}),
        ),
    }
    for filename, chart_function in chart_jobs.items():
        path = chart_dir / filename
        try:
            chart_function(path)
            outputs[filename] = str(path.resolve())
        except Exception as exc:
            outputs[filename] = f"chart_failed: {exc}"
    return outputs


def _worst_rows(rows: list[dict[str, Any]], count: int = 5) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("tanimoto_similarity") is not None,
            float(row.get("tanimoto_similarity") or -1),
        ),
    )[:count]


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "No rows.\n"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, ""))[:80].replace("|", "/") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body]) + "\n"


def _write_report(run_dir: Path, metadata: dict[str, Any], metrics: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    overall = metrics["overall"]
    lines = [
        "# OCSR Benchmark Report",
        "",
        f"- Run time: {metadata.get('run_started_at')}",
        f"- Git commit SHA: {metadata.get('git_commit')}",
        f"- Python version: {metadata.get('python_version')}",
        f"- RDKit version: {metadata.get('rdkit_version')}",
        f"- OCSR backend: {metadata.get('backend')}",
        f"- Model info: {metadata.get('backend_status', {}).get('model_name') or 'not provided'}",
        f"- Input strategy: {metadata.get('preprocessing_strategy')}",
        f"- Identity comparison: {metadata.get('identity_comparison', 'raw')}",
        f"- Standardization profile: {metadata.get('standardization_profile', 'conservative')}",
        f"- Dataset sample count: {overall['total_samples']}",
        f"- Total runtime: {overall.get('total_runtime_ms')} ms",
        "",
        "## Core Metrics",
        "",
        _markdown_table([overall], [
            "total_samples",
            "positive_sample_count",
            "negative_sample_count",
            "recognition_metric_denominator",
            "rejection_metric_denominator",
            "recognition_success_count",
            "recognition_success_rate",
            "rdkit_valid_count",
            "rdkit_valid_rate",
            "valid_smiles_rate",
            "canonical_exact_match_count",
            "canonical_exact_match_rate",
            "stereochemistry_exact_rate",
            "atom_count_error_rate",
            "formal_charge_error_rate",
            "bond_type_error_rate",
            "expected_calibration_error",
            "rejection_coverage",
            "false_accept_rate",
            "review_needed_rate",
            "high_risk_error_review_needed_rate",
            "molecule_equivalent_count",
            "molecule_equivalent_rate",
            "mean_similarity",
            "median_similarity",
            "mean_latency_ms",
            "p50_latency_ms",
            "median_latency_ms",
            "p95_latency_ms",
        ]),
        "## Dataset Sufficiency",
        "",
        _markdown_table([overall], [
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
        ]),
        "## Category Metrics",
        "",
    ]
    category_rows = [
        {"category": category, **group}
        for category, group in metrics.get("groups", {}).get("category", {}).items()
    ]
    lines.append(_markdown_table(category_rows, [
        "category",
        "total_samples",
        "recognition_success_rate",
        "rdkit_valid_rate",
        "canonical_exact_match_rate",
        "molecule_equivalent_rate",
    ]))
    for group_name, label in (
        ("source", "Source Metrics"),
        ("image_quality", "Image Quality Metrics"),
        ("complexity", "Structure Complexity Metrics"),
        ("perturbation", "Perturbation Metrics"),
    ):
        rows_for_group = [
            {group_name: key, **group}
            for key, group in metrics.get("groups", {}).get(group_name, {}).items()
        ]
        lines.extend([
            f"## {label}",
            "",
            _markdown_table(rows_for_group, [
                group_name,
                "total_samples",
                "recognition_success_rate",
                "valid_smiles_rate",
                "canonical_exact_match_rate",
                "stereochemistry_exact_rate",
                "atom_count_error_rate",
                "formal_charge_error_rate",
                "bond_type_error_rate",
            ]),
        ])
    lines.extend([
        "## Ensemble Diagnostics",
        "",
        json.dumps(metrics.get("ensemble", {}), ensure_ascii=False, indent=2),
        "",
        "## Failure Reasons",
        "",
        json.dumps(overall.get("failure_reason_distribution", {}), ensure_ascii=False, indent=2),
        "",
        "## Worst / Lowest Similarity Samples",
        "",
        _markdown_table(_worst_rows(rows), [
            "sample_id",
            "category",
            "ground_truth_smiles",
            "predicted_smiles",
            "recognition_status",
            "failure_reason",
            "tanimoto_similarity",
        ]),
        "## Limitations",
        "",
        str(metadata.get("limitations")),
        "",
        "Do not interpret RDKit parse rate as OCSR recognition accuracy. Accuracy-style rates here are explicitly tied to their denominators.",
    ])
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def write_report_bundle(run_dir: Path, result: dict[str, Any], config_payload: dict[str, Any]) -> dict[str, str]:
    """Write config, predictions, metrics, failures, charts and Markdown report."""
    rows = result["rows"]
    metrics = result["metrics"]
    metadata = result["metadata"]
    (run_dir / "config.json").write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(run_dir / "predictions.csv", rows)
    (run_dir / "metrics.json").write_text(
        json.dumps({"metadata": metadata, "metrics": metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = [row for row in rows if row.get("failure_reason")]
    _write_csv(run_dir / "failure_cases.csv", failures)
    chart_outputs = _write_charts(run_dir, rows, metrics)
    _write_report(run_dir, metadata, metrics, rows)
    return {
        "config": str((run_dir / "config.json").resolve()),
        "predictions": str((run_dir / "predictions.csv").resolve()),
        "metrics": str((run_dir / "metrics.json").resolve()),
        "report": str((run_dir / "report.md").resolve()),
        "failure_cases": str((run_dir / "failure_cases.csv").resolve()),
        "charts": str((run_dir / "charts").resolve()),
        **chart_outputs,
    }
