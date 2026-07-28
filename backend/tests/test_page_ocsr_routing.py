"""Regression tests for raw proposal semantics and complete OCSR routing gates."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from scripts.compare_page_proposal_runs import compare as compare_raw_proposals
from src.documents.models import DocumentRegion
from src.evaluation.page_ocsr_routing import evaluate_page_ocsr_routing, evaluate_routed_boxes
from src.evaluation.page_ocsr_routing_compare import compare_page_ocsr_routing_runs


def _write_document_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _raw_run(root: Path, *, candidate: bool) -> Path:
    root.mkdir(parents=True)
    metrics = {
        "page_count": 30,
        "proposal_count": 752 if candidate else 608,
        "true_positive": 66 if candidate else 50,
        "false_proposal_count": 686 if candidate else 558,
        "molecule_proposal_recall": 0.942857 if candidate else 0.714286,
        "molecule_proposal_precision": 0.087766 if candidate else 0.082237,
        "merged_region_error_count": 4 if candidate else 13,
        "missed_molecule_count": 4 if candidate else 20,
    }
    (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    _write_document_csv(root / "per_document_metrics.csv", [
        {"source_document": "doc1", "recall": 0.9 if candidate else 0.6, "f1": 0.2 if candidate else 0.1},
        {"source_document": "doc2", "recall": 0.9 if candidate else 0.5, "f1": 0.2 if candidate else 0.1},
        {"source_document": "doc3", "recall": 1.0, "f1": 0.1},
    ])
    return root


def test_raw_proposal_gate_exposes_false_growth_and_never_recommends_production(tmp_path: Path) -> None:
    baseline = _raw_run(tmp_path / "baseline", candidate=False)
    candidate = _raw_run(tmp_path / "candidate", candidate=True)
    result = compare_raw_proposals(baseline, candidate, tmp_path / "comparison")
    assert result["gate_name"] == "molecule_raw_proposal_gate"
    assert result["diagnostics"]["proposal_count_delta"] == 144
    assert result["diagnostics"]["false_proposal_delta"] == 128
    assert result["diagnostics"]["true_positives_gained"] == 16
    assert result["diagnostics"]["extra_proposals_per_additional_true_positive"] == 9.0
    assert result["checks"]["false_proposals_not_materially_higher"] is False
    assert result["default_recommendation"] == "proposal=baseline,crop_screening=candidate"
    assert "text_reaction_table_unvalidated" in result["ground_truth_limitations"]


def test_routing_counts_false_accepts_duplicates_and_non_ocsr_decisions() -> None:
    truth = [[0, 0, 100, 100]]
    routed = [
        {"bbox": [0, 0, 100, 100], "decision": "accept_molecule"},
        {"bbox": [0, 0, 100, 100], "decision": "accept_molecule"},
        {"bbox": [150, 150, 220, 220], "decision": "accept_molecule"},
        {"bbox": [0, 0, 100, 100], "decision": "reject_negative"},
        {"bbox": [0, 0, 100, 100], "decision": "review_needed"},
    ]
    result = evaluate_routed_boxes(truth, routed)
    assert len(result["matches"]) == 1
    assert len(result["false_accepted_indices"]) == 2
    assert result["duplicate_accepted_boxes"] == 1
    assert len(result["accepted_items"]) == 3
    assert len(result["rejected_items"]) == 1
    assert len(result["review_needed_items"]) == 1


def test_page_routing_evaluator_writes_complete_outputs(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    (dataset / "pages").mkdir(parents=True)
    Image.new("RGB", (300, 200), "white").save(dataset / "pages" / "doc_p001.png")
    page = {
        "page_id": "doc_p001", "source_document": "doc", "page_number": 1,
        "image_path": "pages/doc_p001.png", "width": 300, "height": 200,
        "annotation_status": "completed",
        "annotations": [{"bbox": [20, 20, 120, 120], "class": "molecule"}],
    }
    (dataset / "annotations.json").write_text(
        json.dumps({"pages": {"doc_p001": page}}), encoding="utf-8",
    )
    (dataset / "protocol.json").write_text(json.dumps({"config_sha256": "cfg"}), encoding="utf-8")

    class FakeDetector:
        def __init__(self, **_kwargs):
            pass

        def propose(self, model_page):
            return [
                DocumentRegion("doc", 1, "p1", (20, 20, 120, 120), "molecule"),
                DocumentRegion("doc", 1, "p2", (160, 20, 260, 120), "molecule"),
                DocumentRegion("doc", 1, "p3", (10, 150, 80, 190), "molecule"),
            ]

    decisions = iter(["accept_molecule", "accept_molecule", "review_needed"])

    def fake_screen(*_args, **_kwargs):
        decision = next(decisions)
        return SimpleNamespace(
            decision=decision,
            recommended_region_type="molecule" if decision == "accept_molecule" else "uncertain",
            screening_score=0.8,
            reason_codes=("possible_molecule",),
        )

    monkeypatch.setattr("src.evaluation.page_ocsr_routing.HeuristicMoleculeRegionDetector", FakeDetector)
    monkeypatch.setattr("src.evaluation.page_ocsr_routing.screen_region_candidate", fake_screen)
    output = tmp_path / "output"
    metrics = evaluate_page_ocsr_routing(
        dataset, output, proposal_config="baseline", crop_screening_config="candidate",
    )
    assert metrics["molecule_routing_recall"] == 1.0
    assert metrics["accepted_box_precision"] == 0.5
    assert metrics["false_accepted_box_count"] == 1
    assert metrics["ocsr_call_count"] == 2
    assert metrics["review_needed_count"] == 1
    assert {path.name for path in output.iterdir()} == {
        "metrics.json", "per_page_metrics.csv", "per_document_metrics.csv",
        "accepted_matches.csv", "false_accepted.csv", "missed_molecules.csv",
        "review_needed.csv", "routing_report.md",
    }


def _routing_run(root: Path, *, candidate: bool) -> Path:
    root.mkdir(parents=True)
    metrics = {
        "molecule_routing_recall": 0.9 if candidate else 0.8,
        "accepted_box_precision": 0.85 if candidate else 0.8,
        "missed_molecule_count": 7 if candidate else 14,
        "false_accepted_box_count": 5,
        "ocsr_call_count": 75 if candidate else 70,
        "ocsr_calls_per_page": 2.5 if candidate else 2.333333,
        "review_needed_count": 20 if candidate else 18,
        "review_needed_per_page": 0.666667 if candidate else 0.6,
        "duplicate_accepted_box_count": 1,
    }
    (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    _write_document_csv(root / "per_document_metrics.csv", [
        {
            "source_document": name,
            "molecule_routing_recall": (0.9 if candidate else 0.8),
            "accepted_box_precision": (0.85 if candidate else 0.8),
            "molecule_routing_f1": (0.87 if candidate else 0.8),
            "false_accepted_boxes": 1,
            "ocsr_calls_per_page": 2.5 if candidate else 2.3,
        }
        for name in ("doc1", "doc2", "doc3")
    ])
    return root


def test_production_routing_gate_requires_workflow_regressions(tmp_path: Path) -> None:
    baseline = _routing_run(tmp_path / "baseline", candidate=False)
    candidate = _routing_run(tmp_path / "candidate", candidate=True)
    blocked = compare_page_ocsr_routing_runs(baseline, candidate, tmp_path / "blocked")
    assert blocked["candidate_passes_production_integration_gate"] is False
    assert blocked["checks"]["document_workflow_regressions_passed"] is False
    passed = compare_page_ocsr_routing_runs(
        baseline, candidate, tmp_path / "passed", workflow_regressions_passed=True,
    )
    assert passed["candidate_passes_production_integration_gate"] is True
    assert "document_layout=baseline" in passed["default_recommendation"]
