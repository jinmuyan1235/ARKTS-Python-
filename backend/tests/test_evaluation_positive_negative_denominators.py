from __future__ import annotations

from typing import Any

from src.evaluation.metrics import compute_metrics, enrich_prediction


def _enriched(row: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "backend": "fake",
        "preprocessing_strategy": "original",
        "category": "unit",
        "source": "unit",
    }
    return enrich_prediction({**defaults, **row}, 0.95)


def test_positive_exact_match_rates_ignore_correctly_rejected_negative_rows() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "positive_correct",
                    "expected_action": "recognize",
                    "ground_truth_smiles": "CCO",
                    "predicted_smiles": "OCC",
                    "recognition_success": True,
                    "recognition_decision": "accepted",
                    "manual_review_recommended": False,
                }
            ),
            _enriched(
                {
                    "sample_id": "negative_rejected",
                    "expected_action": "reject",
                    "ground_truth_smiles": "",
                    "predicted_smiles": None,
                    "recognition_success": False,
                    "recognition_decision": "rejected",
                    "manual_review_recommended": True,
                }
            ),
        ]
    )["overall"]

    assert metrics["positive_sample_count"] == 1
    assert metrics["negative_sample_count"] == 1
    assert metrics["recognition_metric_denominator"] == 1
    assert metrics["rejection_metric_denominator"] == 1
    assert metrics["valid_smiles_rate"] == 1.0
    assert metrics["canonical_exact_match_rate"] == 1.0
    assert metrics["molecule_equivalent_rate"] == 1.0
    assert metrics["rejection_coverage"] == 1.0


def test_negative_hallucination_does_not_increase_positive_valid_rate() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "positive_failed",
                    "expected_action": "recognize",
                    "ground_truth_smiles": "CCO",
                    "predicted_smiles": None,
                    "recognition_success": False,
                    "recognition_decision": "rejected",
                    "manual_review_recommended": True,
                }
            ),
            _enriched(
                {
                    "sample_id": "negative_hallucinated",
                    "expected_action": "reject",
                    "ground_truth_smiles": "",
                    "predicted_smiles": "CCO",
                    "recognition_success": True,
                    "recognition_decision": "accepted",
                    "manual_review_recommended": False,
                }
            ),
        ]
    )["overall"]

    assert metrics["valid_smiles_rate"] == 0.0
    assert metrics["valid_smiles_count"] == 0
    assert metrics["negative_hallucination_count"] == 1
    assert metrics["false_accept_count"] == 1


def test_reviewed_negative_hallucination_is_not_an_automatic_false_accept() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "negative_reviewed_hallucination",
                    "expected_action": "reject",
                    "ground_truth_smiles": "",
                    "predicted_smiles": "CCO",
                    "recognition_success": True,
                    "recognition_decision": "accepted_with_warning",
                    "manual_review_recommended": True,
                }
            )
        ]
    )["overall"]

    assert metrics["negative_hallucination_count"] == 1
    assert metrics["negative_hallucination_rate"] == 1.0
    assert metrics["false_accept_count"] == 0
    assert metrics["false_accept_rate"] == 0.0


def test_automatic_negative_accept_is_a_false_accept() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "negative_auto_accept",
                    "expected_action": "reject",
                    "ground_truth_smiles": "",
                    "predicted_smiles": "CCN",
                    "recognition_success": True,
                    "recognition_decision": "accepted",
                    "manual_review_recommended": False,
                }
            )
        ]
    )["overall"]

    assert metrics["negative_hallucination_count"] == 1
    assert metrics["false_accept_count"] == 1
    assert metrics["false_accept_rate"] == 1.0


def test_all_negative_dataset_marks_positive_metrics_not_applicable() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "negative_rejected",
                    "expected_action": "reject",
                    "ground_truth_smiles": "",
                    "predicted_smiles": None,
                    "recognition_success": False,
                    "recognition_decision": "rejected",
                    "manual_review_recommended": True,
                }
            )
        ]
    )["overall"]

    assert metrics["positive_sample_count"] == 0
    assert metrics["recognition_metric_denominator"] == 0
    assert metrics["valid_smiles_rate"] is None
    assert metrics["canonical_exact_match_rate"] is None
    assert metrics["molecule_equivalent_rate"] is None
    assert metrics["similarity_above_threshold_rate"] is None
    assert metrics["rejection_coverage"] == 1.0


def test_all_positive_dataset_marks_rejection_metrics_not_applicable() -> None:
    metrics = compute_metrics(
        [
            _enriched(
                {
                    "sample_id": "positive_correct",
                    "expected_action": "recognize",
                    "ground_truth_smiles": "CCO",
                    "predicted_smiles": "OCC",
                    "recognition_success": True,
                    "recognition_decision": "accepted",
                    "manual_review_recommended": False,
                }
            )
        ]
    )["overall"]

    assert metrics["negative_sample_count"] == 0
    assert metrics["rejection_metric_denominator"] == 0
    assert metrics["valid_smiles_rate"] == 1.0
    assert metrics["canonical_exact_match_rate"] == 1.0
    assert metrics["rejection_coverage"] is None
    assert metrics["negative_hallucination_rate"] is None
    assert metrics["false_accept_rate"] is None
