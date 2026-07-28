"""Train/dev-only preprocessing profile experiment orchestration."""

from __future__ import annotations

import csv
import gc
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.evaluation.trusted_ocsr import evaluate_trusted_manifest
from src.ocsr.input_normalization import PROFILE_CONFIGS


@dataclass(frozen=True)
class ProfileSelectionConfig:
    minimum_official_gain: float = 0.01
    minimum_perturbation_gain: float = 0.01
    maximum_rendered_regression: float = 0.05
    maximum_p95_latency_ratio: float = 1.5
    maximum_p95_latency_addition_ms: float = 1000.0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _variant_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["image_variant"]: row for row in csv.DictReader(handle)}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) if rows else ["backend", "profile"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def run_preprocessing_experiment(
    manifest: Path,
    output: Path,
    backends: tuple[str, ...] = ("molscribe", "decimer"),
    profiles: tuple[str, ...] = tuple(PROFILE_CONFIGS),
    splits: tuple[str, ...] = ("dev",),
    execute: bool = False,
    retry_failures: bool = True,
    selection_config: ProfileSelectionConfig = ProfileSelectionConfig(),
) -> dict[str, Any]:
    if "test" in splits:
        raise ValueError("Frozen test split cannot participate in preprocessing profile selection.")
    if execute and (len(backends) != 1 or len(profiles) != 1):
        raise ValueError(
            "Execute one backend/profile per Python process so framework device state cannot leak; "
            "the CLI orchestrates this automatically."
        )
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for backend in backends:
        for profile in profiles:
            if profile not in PROFILE_CONFIGS:
                raise ValueError(f"Unknown preprocessing profile: {profile}")
            run_dir = output / "runs" / backend / profile
            if execute and not (run_dir / "metrics.json").is_file():
                evaluate_trusted_manifest(
                    manifest, backend, run_dir, splits=splits, purpose="profile_selection",
                    allow_frozen_test=False, preprocessing_profile=profile,
                    retry_failures=retry_failures,
                )
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                tensorflow = sys.modules.get("tensorflow")
                if tensorflow is not None:
                    try:
                        tensorflow.keras.backend.clear_session()
                    except Exception:
                        pass
            metrics_path = run_dir / "metrics.json"
            variants_path = run_dir / "per_variant_metrics.csv"
            if not metrics_path.is_file() or not variants_path.is_file():
                continue
            payload = _read_json(metrics_path)
            if "test" in payload.get("run_metadata", {}).get("splits", []):
                raise ValueError(f"Test leakage detected in {metrics_path}")
            metrics = payload["metrics"]
            variants = _variant_rows(variants_path)
            rows.append({
                "backend": backend, "profile": profile,
                "profile_sha256": payload["run_metadata"].get("preprocessing_config_sha256"),
                "splits": ";".join(payload["run_metadata"].get("splits", [])),
                "sample_count": metrics["sample_count"],
                "backend_execution_success_rate": metrics["backend_execution_success_rate"],
                "backend_success_rate": metrics["backend_success_rate"],
                "overall_connectivity_exact": metrics["connectivity_exact_rate"],
                "overall_full_inchikey_exact": metrics["full_inchikey_exact_rate"],
                "conditional_full_inchikey_exact": metrics["conditional_full_inchikey_exact_rate"],
                "official_full_inchikey_exact": variants.get("official_clean", {}).get("inchikey_exact_match_rate"),
                "rendered_full_inchikey_exact": variants.get("rendered_clean", {}).get("inchikey_exact_match_rate"),
                "perturbation_full_inchikey_exact": variants.get("synthetic_perturbation", {}).get("inchikey_exact_match_rate"),
                "p95_latency_ms": metrics["p95_latency_ms"],
            })
    best: dict[str, Any] = {}
    for backend in backends:
        backend_rows = [row for row in rows if row["backend"] == backend]
        baseline = next((row for row in backend_rows if row["profile"] == "raw"), None)
        if not baseline: continue
        eligible = []
        for row in backend_rows:
            success_ok = float(row["backend_execution_success_rate"]) >= float(baseline["backend_execution_success_rate"])
            rendered_ok = float(row["rendered_full_inchikey_exact"] or 0) >= (
                float(baseline["rendered_full_inchikey_exact"] or 0) - selection_config.maximum_rendered_regression
            )
            official_gain = float(row["official_full_inchikey_exact"] or 0) - float(baseline["official_full_inchikey_exact"] or 0)
            perturbation_gain = float(row["perturbation_full_inchikey_exact"] or 0) - float(baseline["perturbation_full_inchikey_exact"] or 0)
            domain_shift_ok = (
                row["profile"] == "raw"
                or (
                    official_gain >= selection_config.minimum_official_gain
                    and perturbation_gain >= selection_config.minimum_perturbation_gain
                )
            )
            baseline_p95 = float(baseline["p95_latency_ms"] or 0)
            latency_limit = baseline_p95 * selection_config.maximum_p95_latency_ratio + selection_config.maximum_p95_latency_addition_ms
            latency_ok = row["profile"] == "raw" or float(row["p95_latency_ms"] or 0) <= latency_limit
            row["success_not_lower"] = success_ok
            row["rendered_not_severely_lower"] = rendered_ok
            row["official_gain"] = round(official_gain, 6)
            row["perturbation_gain"] = round(perturbation_gain, 6)
            row["official_and_perturbation_improve"] = domain_shift_ok
            row["p95_latency_acceptable"] = latency_ok
            if success_ok and rendered_ok and domain_shift_ok and latency_ok: eligible.append(row)
        chosen = max(eligible or [baseline], key=lambda row: (
            float(row["overall_full_inchikey_exact"]),
            float(row["official_full_inchikey_exact"] or 0) + float(row["perturbation_full_inchikey_exact"] or 0),
            -float(row["p95_latency_ms"] or 0),
        ))
        best[backend] = {
            "profile": chosen["profile"], "profile_sha256": chosen["profile_sha256"],
            "profile_config": asdict(PROFILE_CONFIGS[str(chosen["profile"])]),
            "selection_splits": list(splits), "frozen_test_used": False,
            "selection_config": asdict(selection_config),
            "selection_metrics": chosen,
        }
    _write_csv(output / "experiment_matrix.csv", rows)
    (output / "best_profiles.json").write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# OCSR preprocessing selection (train/dev only)", "", f"Splits: {', '.join(splits)}", "", "Frozen v0.1 test was not read.", ""]
    for backend, selection in best.items():
        report.append(f"- {backend}: {selection['profile']} ({selection['profile_sha256']})")
    report += ["", "Overall metrics include backend failures. Conditional metrics are supplementary and are not used to hide failures."]
    (output / "comparison.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {"runs": len(rows), "best_profiles": best, "output": str(output)}
