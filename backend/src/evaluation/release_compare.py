"""Compare fixed OCSR release benchmark runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


METRIC_DIRECTIONS = {
    "valid_smiles_rate": "higher",
    "canonical_exact_match_rate": "higher",
    "stereochemistry_exact_rate": "higher",
    "false_accept_rate": "lower",
    "high_risk_error_review_needed_rate": "higher",
    "p50_latency_ms": "lower",
    "p95_latency_ms": "lower",
}


def load_metrics_file(path: str | Path) -> dict[str, Any]:
    """Load one release metrics JSON file."""
    return json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))


def discover_metrics_files(release_dir: str | Path) -> dict[str, Path]:
    """Return backend name to metrics file for a release directory."""
    root = Path(release_dir).expanduser().resolve()
    return {
        path.name.removesuffix("_metrics.json"): path
        for path in sorted(root.glob("*_metrics.json"))
    }


def compare_release_dirs(
    current_dir: str | Path,
    previous_dir: str | Path,
    rate_tolerance: float = 0.0,
    latency_tolerance_ms: float = 0.0,
) -> dict[str, Any]:
    """Compare all matching backend metrics in two release directories."""
    current_files = discover_metrics_files(current_dir)
    previous_files = discover_metrics_files(previous_dir)
    comparisons = []
    for backend in sorted(set(current_files) & set(previous_files)):
        comparisons.extend(
            compare_metric_payloads(
                backend,
                load_metrics_file(current_files[backend]),
                load_metrics_file(previous_files[backend]),
                rate_tolerance=rate_tolerance,
                latency_tolerance_ms=latency_tolerance_ms,
            )
        )
    missing_current = sorted(set(previous_files) - set(current_files))
    missing_previous = sorted(set(current_files) - set(previous_files))
    return {
        "passed": not any(item["regressed"] for item in comparisons) and not missing_current,
        "comparisons": comparisons,
        "missing_current_backends": missing_current,
        "missing_previous_backends": missing_previous,
    }


def compare_metric_payloads(
    backend: str,
    current_payload: dict[str, Any],
    previous_payload: dict[str, Any],
    rate_tolerance: float = 0.0,
    latency_tolerance_ms: float = 0.0,
) -> list[dict[str, Any]]:
    """Compare one backend's current and previous metric payloads."""
    current = _overall(current_payload)
    previous = _overall(previous_payload)
    rows: list[dict[str, Any]] = []
    for metric, direction in METRIC_DIRECTIONS.items():
        current_value = _float_or_none(current.get(metric))
        previous_value = _float_or_none(previous.get(metric))
        delta = None if current_value is None or previous_value is None else round(current_value - previous_value, 6)
        tolerance = latency_tolerance_ms if metric.endswith("_ms") else rate_tolerance
        regressed = False
        if delta is not None:
            if direction == "higher":
                regressed = delta < -abs(tolerance)
            else:
                regressed = delta > abs(tolerance)
        rows.append({
            "backend": backend,
            "metric": metric,
            "direction": direction,
            "previous": previous_value,
            "current": current_value,
            "delta": delta,
            "tolerance": tolerance,
            "regressed": regressed,
        })
    return rows


def write_comparison_report(path: str | Path, comparison: dict[str, Any]) -> None:
    """Write a Markdown report for release comparison."""
    lines = [
        "# OCSR Release Comparison",
        "",
        f"- Overall: {'PASS' if comparison.get('passed') else 'FAIL'}",
        f"- Missing current backends: {', '.join(comparison.get('missing_current_backends') or []) or '-'}",
        f"- New backends without previous baseline: {', '.join(comparison.get('missing_previous_backends') or []) or '-'}",
        "",
        "| Backend | Metric | Direction | Previous | Current | Delta | Regressed |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in comparison.get("comparisons") or []:
        lines.append(
            "| "
            + " | ".join(
                str(row.get(key, ""))
                for key in ("backend", "metric", "direction", "previous", "current", "delta", "regressed")
            )
            + " |"
        )
    Path(path).expanduser().resolve().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _overall(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    return dict((metrics or {}).get("overall") or metrics or {})


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
