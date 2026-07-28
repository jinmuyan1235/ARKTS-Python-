"""Frozen-rule ensemble overlap analysis using development predictions only."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _bool(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _load(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if any(row.get("split") == "test" for row in rows):
        raise ValueError("Frozen test predictions cannot participate in ensemble/router analysis.")
    return {row["sample_id"]: row for row in rows}


def analyze_development_overlap(molscribe: Path, decimer: Path, output: Path) -> dict[str, Any]:
    mol = _load(molscribe); dec = _load(decimer)
    shared = sorted(set(mol) & set(dec))
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for sample_id in shared:
        mol_correct = _bool(mol[sample_id].get("full_inchikey_exact", mol[sample_id].get("inchikey_exact_match")))
        dec_correct = _bool(dec[sample_id].get("full_inchikey_exact", dec[sample_id].get("inchikey_exact_match")))
        mol_key = mol[sample_id].get("predicted_inchikey") or mol[sample_id].get("predicted_canonical_smiles")
        dec_key = dec[sample_id].get("predicted_inchikey") or dec[sample_id].get("predicted_canonical_smiles")
        agree = bool(mol_key and dec_key and mol_key == dec_key)
        if mol_correct and dec_correct: category = "both_correct"
        elif mol_correct: category = "only_molscribe_correct"
        elif dec_correct: category = "only_decimer_correct"
        elif agree: category = "both_wrong_but_agree"
        else: category = "both_wrong_and_disagree"
        counts[category] += 1
        rows.append({"sample_id": sample_id, "category": category, "outputs_agree": agree})
    output.mkdir(parents=True, exist_ok=True)
    with (output / "backend_overlap_dev.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("sample_id", "category", "outputs_agree")); writer.writeheader(); writer.writerows(rows)
    payload = {
        "sample_count": len(rows), "category_counts": dict(sorted(counts.items())),
        "frozen_test_used": False, "agreement_is_not_correctness_evidence": True,
        "candidate_router_created": False,
        "decision": "Keep the existing ensemble as baseline; this analysis does not change production routing.",
    }
    (output / "ensemble_dev_analysis.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "ensemble_dev_analysis.md").write_text(
        "# Development-only ensemble overlap\n\n"
        + "\n".join(f"- {key}: {value}" for key, value in sorted(counts.items()))
        + "\n\nModel agreement is not treated as evidence of correctness. The frozen test was not read, and the existing ensemble remains the baseline.\n",
        encoding="utf-8",
    )
    return payload
