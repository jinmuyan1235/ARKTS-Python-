"""Compare molecule-only raw page-proposal runs without making a production claim."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MoleculeRawProposalGateConfig:
    """Diagnostic limits for the molecule-only raw proposal comparison."""

    max_false_proposal_ratio: float = 1.10
    max_proposals_per_page: float = 30.0
    max_extra_proposals_per_additional_true_positive: float = 12.0
    max_proposal_count_ratio: float = 1.50
    severe_document_recall_delta: float = -0.10


DEFAULT_RAW_PROPOSAL_GATE_CONFIG = MoleculeRawProposalGateConfig()


def _load_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["source_document"]: row for row in csv.DictReader(handle)}


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def compare(
    baseline: Path,
    candidate: Path,
    output: Path,
    *,
    gate_config: MoleculeRawProposalGateConfig = DEFAULT_RAW_PROPOSAL_GATE_CONFIG,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    first = json.loads((baseline / "metrics.json").read_text(encoding="utf-8"))
    second = json.loads((candidate / "metrics.json").read_text(encoding="utf-8"))
    first_docs = _load_csv(baseline / "per_document_metrics.csv")
    second_docs = _load_csv(candidate / "per_document_metrics.csv")
    document_changes = []
    improved_documents = 0; severe_regressions = 0
    for document in sorted(set(first_docs) | set(second_docs)):
        old = first_docs.get(document, {}); new = second_docs.get(document, {})
        recall_delta = float(new.get("recall") or 0) - float(old.get("recall") or 0)
        f1_delta = float(new.get("f1") or 0) - float(old.get("f1") or 0)
        improved_documents += int(f1_delta > 0)
        severe_regressions += int(recall_delta < gate_config.severe_document_recall_delta)
        document_changes.append({"source_document": document, "recall_delta": recall_delta, "f1_delta": f1_delta})
    page_count = max(int(second.get("page_count") or 0), 1)
    proposal_delta = int(second["proposal_count"]) - int(first["proposal_count"])
    false_delta = int(second["false_proposal_count"]) - int(first["false_proposal_count"])
    true_positives_gained = int(second["true_positive"]) - int(first["true_positive"])
    proposal_ratio = _safe_ratio(float(second["proposal_count"]), float(first["proposal_count"]))
    false_ratio = _safe_ratio(float(second["false_proposal_count"]), float(first["false_proposal_count"]))
    extra_per_tp = _safe_ratio(float(max(proposal_delta, 0)), float(max(true_positives_gained, 0)))
    diagnostics = {
        "proposal_count_delta": proposal_delta,
        "proposal_count_ratio": proposal_ratio,
        "false_proposal_delta": false_delta,
        "false_proposal_ratio": false_ratio,
        "baseline_proposals_per_page": float(first["proposal_count"]) / page_count,
        "candidate_proposals_per_page": float(second["proposal_count"]) / page_count,
        "baseline_false_proposals_per_page": float(first["false_proposal_count"]) / page_count,
        "candidate_false_proposals_per_page": float(second["false_proposal_count"]) / page_count,
        "proposals_per_page": {
            "baseline": float(first["proposal_count"]) / page_count,
            "candidate": float(second["proposal_count"]) / page_count,
        },
        "false_proposals_per_page": {
            "baseline": float(first["false_proposal_count"]) / page_count,
            "candidate": float(second["false_proposal_count"]) / page_count,
        },
        "true_positives_gained": true_positives_gained,
        "extra_proposals_per_additional_true_positive": extra_per_tp,
    }
    checks = {
        "recall_not_lower": second["molecule_proposal_recall"] >= first["molecule_proposal_recall"],
        "precision_not_materially_lower": second["molecule_proposal_precision"] >= first["molecule_proposal_precision"] - 0.02,
        "merged_errors_not_higher": second["merged_region_error_count"] <= first["merged_region_error_count"],
        "missed_molecules_not_higher": second["missed_molecule_count"] <= first["missed_molecule_count"],
        "at_least_two_documents_improve": improved_documents >= 2,
        "no_severe_document_recall_regression": severe_regressions == 0,
        "proposal_count_not_exploded": (
            proposal_ratio is None or proposal_ratio <= gate_config.max_proposal_count_ratio
        ),
        "false_proposals_not_materially_higher": (
            false_ratio is None or false_ratio <= gate_config.max_false_proposal_ratio
        ),
        "proposals_per_page_not_excessive": (
            diagnostics["candidate_proposals_per_page"] <= gate_config.max_proposals_per_page
        ),
        "extra_proposals_per_additional_true_positive_within_limit": (
            extra_per_tp is not None
            and extra_per_tp <= gate_config.max_extra_proposals_per_additional_true_positive
        ),
    }
    promising = all(checks.values())
    result = {
        "gate_name": "molecule_raw_proposal_gate",
        "scope": "molecule-only raw bbox proposals on annotated pages",
        "ground_truth_limitations": {
            "only_molecule_annotated": True,
            "text_reaction_table_unvalidated": True,
            "raw_proposal_pass_does_not_imply_production_integration_pass": True,
        },
        "gate_config": asdict(gate_config),
        "baseline": first,
        "candidate": second,
        "diagnostics": diagnostics,
        "checks": checks,
        "molecule_raw_proposal_gate": {"passed": promising, "checks": checks},
        "candidate_raw_proposal_is_promising": promising,
        "per_document_changes": document_changes,
        "default_recommendation": "proposal=baseline,crop_screening=candidate",
    }
    (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Molecule raw proposal gate",
        "",
        f"Candidate raw proposal is promising: **{promising}**",
        "",
        "> This gate only evaluates molecule ground-truth boxes. Text, reaction, table, figure, and other layout regions were not annotated. A raw proposal pass is not a production integration pass.",
        "",
        "## Diagnostics",
        "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in diagnostics.items())
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {name}: {passed}" for name, passed in checks.items())
    lines.extend([
        "",
        f"Production default remains: `{result['default_recommendation']}`",
        "",
        "A separate full proposal + crop-screening routing gate and document-layout regression suite are required before changing production defaults.",
        "",
    ])
    (output / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True); parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True); args = parser.parse_args()
    result = compare(Path(args.baseline), Path(args.candidate), Path(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
