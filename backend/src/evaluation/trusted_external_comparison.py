"""Compare frozen external-holdout runs without tuning from their labels."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping


PRIMARY_FIELDS = (
    "backend_execution_success_rate", "backend_success_rate", "connectivity_exact_rate",
    "full_inchikey_exact_rate", "nonisomeric_canonical_exact_rate", "isomeric_exact_match_rate",
    "mean_latency_ms", "p95_latency_ms", "peak_gpu_memory_mib",
    "false_acceptance_rate", "abstention_rate",
)


def compare_external_runs(runs: Mapping[str, Path], output: Path) -> dict[str, Any]:
    summaries: dict[str, dict[str, Any]] = {}
    error_rows: list[dict[str, Any]] = []
    for name, directory in runs.items():
        payload = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        metadata = payload.get("run_metadata", {})
        if metadata.get("purpose") != "external_holdout" or metadata.get("splits") != ["external_holdout"]:
            raise ValueError(f"Run {name} is not a frozen external_holdout result.")
        metrics = payload["metrics"]
        summaries[name] = {
            "backend": metadata.get("backend"),
            "preprocessing_profile": (metadata.get("preprocessing_profile") or {}).get("profile"),
            **{field: metrics.get(field) for field in PRIMARY_FIELDS},
        }
        for error, count in metrics.get("error_distribution", {}).items():
            error_rows.append({"run": name, "error_type": error, "count": count})
    ranking = sorted(
        summaries,
        key=lambda name: (
            float(summaries[name].get("full_inchikey_exact_rate") or 0),
            float(summaries[name].get("connectivity_exact_rate") or 0),
        ),
        reverse=True,
    )
    result = {
        "dataset_role": "external_holdout",
        "runs": summaries,
        "ranking_by_full_inchikey_then_connectivity": ranking,
        "test_used_for_tuning": False,
        "claims_must_use_external_holdout_only": True,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "error_distribution.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("run", "error_type", "count")); writer.writeheader(); writer.writerows(error_rows)
    lines = ["# OCSR trusted external-holdout comparison", "", "No external-holdout label was used to select a profile or change routing.", ""]
    for rank, name in enumerate(ranking, 1):
        item = summaries[name]
        lines.append(
            f"{rank}. {name}: full InChIKey={item['full_inchikey_exact_rate']}, "
            f"connectivity={item['connectivity_exact_rate']}, execution success={item['backend_execution_success_rate']}"
        )
    lines += [
        "", "These PubChem/RDKit/synthetic results do not estimate real-PMC crop accuracy, handwritten/Markush accuracy, or page detection recall.",
    ]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
