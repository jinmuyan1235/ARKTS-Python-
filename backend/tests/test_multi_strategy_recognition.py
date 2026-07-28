"""Tests for conditional multi-preprocessing OCSR retries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from src.analysis.multi_strategy_recognition import recognize_with_fallback_strategies
from src.analysis.recognition_decision import decide_recognition
from src.ocsr.base import OCSRResult


class FakeRecognizer:
    preferred_image_stage = "original"

    def __init__(self, results: list[OCSRResult]) -> None:
        self.results = list(results)
        self.calls: list[Any] = []

    def recognize(self, target: Any) -> OCSRResult:
        self.calls.append(target)
        return self.results.pop(0)


def _stages() -> dict[str, np.ndarray]:
    image = np.full((256, 256), 255, dtype=np.uint8)
    enhanced = image.copy()
    enhanced[48:54, 40:216] = 0
    enhanced[48:216, 122:128] = 0
    return {
        "original": image,
        "clarity_enhanced": enhanced,
        "normalized": enhanced,
        "gray": enhanced,
        "binary": enhanced,
    }


def _quality(**updates: Any) -> dict[str, Any]:
    payload = {"passed": True, "quality_score": 0.9, "reason_codes": []}
    payload.update(updates)
    return payload


def _result(smiles: str | None, status: str = "success", confidence: float | None = 0.9) -> OCSRResult:
    return OCSRResult(
        smiles=smiles,
        confidence=confidence,
        backend="molscribe",
        status=status,  # type: ignore[arg-type]
        message="done" if status == "success" else "failed",
        inference_time_ms=1.0,
    )


def test_successful_first_attempt_stops_without_retry(tmp_path: Path) -> None:
    recognizer = FakeRecognizer([_result("CCO")])

    result = recognize_with_fallback_strategies(
        recognizer, tmp_path / "mol.png", _stages(), image_quality=_quality()
    )

    assert len(recognizer.calls) == 1
    assert result.selected_strategy == "original"
    assert result.strategy_agreement is None
    assert result.report_fields()["strategy_attempt_count"] == 1


def test_failed_attempt_retries_next_strategy(tmp_path: Path) -> None:
    recognizer = FakeRecognizer([
        _result(None, status="failed", confidence=None),
        _result("CCO"),
    ])

    result = recognize_with_fallback_strategies(
        recognizer,
        tmp_path / "mol.png",
        _stages(),
        image_quality=_quality(),
        strategies=["original", "normalized", "binary"],
    )

    assert len(recognizer.calls) == 2
    assert result.selected_strategy == "normalized"
    assert result.attempts[0]["triggered_next_strategy"] is True
    assert "recognition_failed" in result.attempts[0]["retry_reason_codes"]


def test_blurred_valid_result_retries_a_clearer_input(tmp_path: Path) -> None:
    recognizer = FakeRecognizer([_result("CCO"), _result("OCC")])

    result = recognize_with_fallback_strategies(
        recognizer,
        tmp_path / "mol.png",
        _stages(),
        image_quality=_quality(passed=False, quality_score=0.4, reason_codes=["low_contrast"]),
        strategies=["original", "enhanced"],
    )

    assert len(recognizer.calls) == 2
    assert result.selected_strategy == "enhanced"
    assert "image_quality_low_contrast" in result.attempts[0]["retry_reason_codes"]


def test_strategy_disagreement_requires_review() -> None:
    report = {
        "input": {"type": "image", "filename": "mol.png"},
        "status": "success",
        "ocsr": {
            "backend": "molscribe",
            "status": "success",
            "smiles": "CCO",
            "predicted_smiles": "CCO",
            "strategy_agreement": False,
        },
        "validation": {"valid": True, "canonical_smiles": "CCO"},
        "chemical_identity": {"fragment_count": 1, "formal_charge": 0, "stereocenter_count": 0},
        "structure_warnings": [],
        "image_quality": {"quality_score": 0.88, "passed": True, "reason_codes": []},
    }

    decision = decide_recognition(report)

    assert decision["decision"] == "review_needed"
    assert "strategy_disagreement" in decision["reason_codes"]
