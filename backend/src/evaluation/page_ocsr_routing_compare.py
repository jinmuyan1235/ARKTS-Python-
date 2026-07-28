"""Production integration gate for complete page proposal + crop routing runs."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PageOCSRRoutingGateConfig:
    max_ocsr_call_ratio: float = 1.10
    max_ocsr_calls_per_page_absolute_increase: float = 0.25
    max_review_needed_per_page: float = 5.0
    severe_document_recall_delta: float = -0.10


DEFAULT_ROUTING_GATE_CONFIG = PageOCSRRoutingGateConfig()


def _load_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["source_document"]: row for row in csv.DictReader(handle)}


def compare_page_ocsr_routing_runs(
    baseline: Path,
    candidate: Path,
    output: Path,
    *,
    workflow_regressions_passed: bool = False,
    gate_config: PageOCSRRoutingGateConfig = DEFAULT_ROUTING_GATE_CONFIG,
) -> dict[str, Any]:
    baseline = baseline.resolve()
    candidate = candidate.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    first = json.loads((baseline / "metrics.json").read_text(encoding="utf-8"))
    second = json.loads((candidate / "metrics.json").read_text(encoding="utf-8"))
    first_docs = _load_csv(baseline / "per_document_metrics.csv")
    second_docs = _load_csv(candidate / "per_document_metrics.csv")
    document_changes = []
    improved_documents = 0
    severe_regressions = 0
    for document in sorted(set(first_docs) | set(second_docs)):
        old = first_docs.get(document, {})
        new = second_docs.get(document, {})
        recall_delta = float(new.get("molecule_routing_recall") or 0) - float(
            old.get("molecule_routing_recall") or 0,
        )
        precision_delta = float(new.get("accepted_box_precision") or 0) - float(
            old.get("accepted_box_precision") or 0,
        )
        f1_delta = float(new.get("molecule_routing_f1") or 0) - float(
            old.get("molecule_routing_f1") or 0,
        )
        false_delta = int(new.get("false_accepted_boxes") or 0) - int(
            old.get("false_accepted_boxes") or 0,
        )
        improved_documents += int(f1_delta > 0)
        severe_regressions += int(recall_delta < gate_config.severe_document_recall_delta)
        document_changes.append({
            "source_document": document,
            "recall_delta": recall_delta,
            "precision_delta": precision_delta,
            "f1_delta": f1_delta,
            "false_accepted_delta": false_delta,
            "ocsr_calls_per_page_delta": float(new.get("ocsr_calls_per_page") or 0) - float(
                old.get("ocsr_calls_per_page") or 0,
            ),
        })
    call_limit = max(
        float(first["ocsr_calls_per_page"]) * gate_config.max_ocsr_call_ratio,
        float(first["ocsr_calls_per_page"]) + gate_config.max_ocsr_calls_per_page_absolute_increase,
    )
    checks = {
        "molecule_routing_recall_not_lower": (
            second["molecule_routing_recall"] >= first["molecule_routing_recall"]
        ),
        "missed_molecules_reduced": second["missed_molecule_count"] < first["missed_molecule_count"],
        "false_accepted_boxes_not_higher": (
            second["false_accepted_box_count"] <= first["false_accepted_box_count"]
        ),
        "accepted_box_precision_not_lower": (
            second["accepted_box_precision"] >= first["accepted_box_precision"]
        ),
        "ocsr_calls_per_page_not_materially_higher": second["ocsr_calls_per_page"] <= call_limit,
        "duplicate_accepted_boxes_not_higher": (
            second["duplicate_accepted_box_count"] <= first["duplicate_accepted_box_count"]
        ),
        "review_needed_within_limit": (
            second["review_needed_per_page"] <= gate_config.max_review_needed_per_page
        ),
        "at_least_two_documents_improve": improved_documents >= 2,
        "no_severe_document_recall_regression": severe_regressions == 0,
        "document_workflow_regressions_passed": bool(workflow_regressions_passed),
    }
    passed = all(checks.values())
    deltas = {
        "molecule_routing_recall_delta": (
            second["molecule_routing_recall"] - first["molecule_routing_recall"]
        ),
        "accepted_box_precision_delta": (
            second["accepted_box_precision"] - first["accepted_box_precision"]
        ),
        "missed_molecule_delta": second["missed_molecule_count"] - first["missed_molecule_count"],
        "false_accepted_box_delta": (
            second["false_accepted_box_count"] - first["false_accepted_box_count"]
        ),
        "ocsr_call_delta": second["ocsr_call_count"] - first["ocsr_call_count"],
        "ocsr_calls_per_page_delta": second["ocsr_calls_per_page"] - first["ocsr_calls_per_page"],
        "review_needed_delta": second["review_needed_count"] - first["review_needed_count"],
        "duplicate_accepted_box_delta": (
            second["duplicate_accepted_box_count"] - first["duplicate_accepted_box_count"]
        ),
    }
    result = {
        "gate_name": "page_ocsr_routing_production_integration_gate",
        "gate_config": asdict(gate_config),
        "baseline": first,
        "candidate": second,
        "deltas": deltas,
        "checks": checks,
        "per_document_changes": document_changes,
        "candidate_passes_production_integration_gate": passed,
        "default_recommendation": (
            "molecule_proposal=candidate,crop_screening=candidate,document_layout=baseline"
            if passed
            else "proposal=baseline,crop_screening=candidate"
        ),
    }
    (output / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    lines = [
        "# Page OCSR routing production integration gate",
        "",
        f"Pass: **{passed}**",
        "",
        "## Deltas",
        "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in deltas.items())
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {name}: {value}" for name, value in checks.items())
    lines.extend(["", f"Recommended default: `{result['default_recommendation']}`", ""])
    (output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return result
