"""Conditional multi-image-strategy retry orchestration for OCSR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import config
from src.analysis.image_quality import assess_image_quality
from src.chem.smiles_validator import validate_smiles
from src.ocsr.base import OCSRResult
from src.ocsr.recognizer import MoleculeRecognizer


VALID_STRATEGIES = ("original", "enhanced", "normalized", "grayscale", "binary")
STAGE_BY_STRATEGY = {
    "original": "original",
    "enhanced": "clarity_enhanced",
    "normalized": "normalized",
    "grayscale": "gray",
    "binary": "binary",
}
QUALITY_RETRY_REASONS = {
    "low_resolution",
    "low_contrast",
    "blurred",
    "too_little_foreground",
    "too_dense_foreground",
    "possibly_cropped",
}


@dataclass(frozen=True)
class MultiStrategyRecognitionResult:
    """The selected OCSR result plus retry trace fields for the report."""

    result: OCSRResult
    attempts: list[dict[str, Any]]
    selected_strategy: str | None
    strategy_agreement: bool | None

    def report_fields(self) -> dict[str, Any]:
        """Return JSON-friendly fields to merge into the report OCSR block."""
        attempted = [attempt["strategy"] for attempt in self.attempts]
        return {
            "selected_strategy": self.selected_strategy,
            "preprocessing_strategy": self.selected_strategy,
            "attempted_strategies": attempted,
            "strategy_attempt_count": len(self.attempts),
            "strategy_attempts": self.attempts,
            "strategy_agreement": self.strategy_agreement,
        }


def configured_strategies(
    configured: Sequence[str] | None = None,
    *,
    preferred_first: str | None = None,
) -> list[str]:
    """Return a de-duplicated, valid strategy order."""
    raw = configured if configured is not None else config.OCSR_FALLBACK_IMAGE_STRATEGIES
    strategies: list[str] = []
    for item in raw:
        strategy = str(item).strip().lower()
        if strategy in VALID_STRATEGIES and strategy not in strategies:
            strategies.append(strategy)
    if not strategies:
        strategies = ["original"]
    if preferred_first in strategies:
        strategies = [preferred_first, *[strategy for strategy in strategies if strategy != preferred_first]]
    return strategies


def preferred_strategy_for_recognizer(recognizer: MoleculeRecognizer) -> str:
    """Map the recognizer's legacy preferred stage to a retry strategy name."""
    return "original" if recognizer.preferred_image_stage == "original" else "normalized"


def recognize_with_fallback_strategies(
    recognizer: MoleculeRecognizer,
    original_path: str | Path,
    preprocessing_stages: Mapping[str, np.ndarray],
    stage_paths: Mapping[str, str] | None = None,
    image_quality: Mapping[str, Any] | None = None,
    strategies: Sequence[str] | None = None,
) -> MultiStrategyRecognitionResult:
    """Run OCSR once, then conditionally retry with alternate preprocessing stages."""
    ordered_strategies = configured_strategies(
        strategies,
        preferred_first=preferred_strategy_for_recognizer(recognizer),
    )
    attempts: list[dict[str, Any]] = []
    result_by_strategy: dict[str, OCSRResult] = {}

    for attempt_index, strategy in enumerate(ordered_strategies, start=1):
        target = _target_for_strategy(strategy, original_path, preprocessing_stages, stage_paths or {})
        result = recognizer.recognize(target)
        attempt = _attempt_from_result(strategy, target, result)
        attempt["attempt_index"] = attempt_index
        retry_reasons = retry_reason_codes(attempt, attempt.get("image_quality") or image_quality)
        attempt["retry_reason_codes"] = retry_reasons
        attempt["triggered_next_strategy"] = bool(retry_reasons)
        attempts.append(attempt)
        result_by_strategy[strategy] = result
        if _can_stop_after_attempt(attempt, retry_reasons):
            break

    selected = _select_attempt(attempts)
    selected_strategy = selected.get("strategy") if selected else attempts[-1]["strategy"]
    selected_result = result_by_strategy.get(str(selected_strategy), result_by_strategy[attempts[-1]["strategy"]])
    return MultiStrategyRecognitionResult(
        result=selected_result,
        attempts=attempts,
        selected_strategy=str(selected_strategy) if selected_strategy else None,
        strategy_agreement=_strategy_agreement(attempts),
    )


def retry_reason_codes(attempt: Mapping[str, Any], image_quality: Mapping[str, Any] | None = None) -> list[str]:
    """Explain why another strategy should be attempted after this result."""
    reasons: list[str] = []
    if attempt.get("status") != "success":
        reasons.append("recognition_failed")
    if not attempt.get("smiles"):
        reasons.append("missing_smiles")
    if attempt.get("smiles") and not attempt.get("valid_smiles"):
        reasons.append("rdkit_invalid")
    confidence = attempt.get("confidence")
    if confidence is not None:
        try:
            if float(confidence) < config.DECISION_REVIEW_THRESHOLD:
                reasons.append("low_confidence")
        except (TypeError, ValueError):
            pass
    if attempt.get("decision") == "review_needed":
        reasons.append("result_review_needed")
    consensus = attempt.get("consensus") or {}
    if isinstance(consensus, Mapping) and consensus.get("decision") == "review_needed":
        reasons.append("result_review_needed")

    result_is_usable = bool(
        attempt.get("status") == "success"
        and attempt.get("smiles")
        and attempt.get("valid_smiles")
        and "low_confidence" not in reasons
    )
    quality = dict(image_quality or {})
    quality_reasons = set(str(item) for item in quality.get("reason_codes") or [])
    if attempt.get("strategy") != "original":
        # Percentile-based contrast is intentionally conservative for uploads,
        # but normalized sparse line art can look "low contrast" merely because
        # most pixels are white. Do not cascade retries on that derived stage.
        quality_reasons.discard("low_contrast")
    # A syntactically valid SMILES can still omit atoms when the source is
    # blurred or low contrast. Continue with a cleaner image stage in that
    # case; selection later prefers the valid attempt whose actual input no
    # longer carries the quality warning.
    quality_retry_reasons = quality_reasons & QUALITY_RETRY_REASONS
    if quality_retry_reasons and (not result_is_usable or quality_retry_reasons & {"blurred", "low_contrast"}):
        reasons.extend(f"image_quality_{item}" for item in sorted(quality_retry_reasons))
    if not result_is_usable and quality.get("passed") is False and not quality_reasons:
        reasons.append("image_quality_failed")
    return sorted(set(reasons))


def _can_stop_after_attempt(attempt: Mapping[str, Any], retry_reasons: Sequence[str]) -> bool:
    return bool(attempt.get("status") == "success" and attempt.get("valid_smiles") and not retry_reasons)


def _target_for_strategy(
    strategy: str,
    original_path: str | Path,
    preprocessing_stages: Mapping[str, np.ndarray],
    stage_paths: Mapping[str, str],
) -> str | Path | np.ndarray:
    stage = STAGE_BY_STRATEGY[strategy]
    if strategy == "original":
        return Path(original_path).expanduser().resolve()
    if stage in stage_paths:
        return stage_paths[stage]
    if stage in preprocessing_stages:
        return preprocessing_stages[stage]
    return Path(original_path).expanduser().resolve()


def _target_label(target: str | Path | np.ndarray) -> str:
    if isinstance(target, np.ndarray):
        return f"array:{target.shape}"
    return str(Path(target).expanduser().resolve())


def _attempt_from_result(strategy: str, target: str | Path | np.ndarray, result: OCSRResult) -> dict[str, Any]:
    validation = validate_smiles(result.smiles)
    raw = result.to_dict()
    try:
        input_quality = assess_image_quality(target)
    except Exception:
        input_quality = None
    return {
        "strategy": strategy,
        "image_stage": STAGE_BY_STRATEGY[strategy],
        "image_input": _target_label(target),
        "backend": result.backend,
        "status": result.status,
        "smiles": result.smiles,
        "raw_output": result.raw_output,
        "canonical_smiles": validation["canonical_smiles"] if validation["valid"] else None,
        "valid_smiles": bool(validation["valid"]),
        "validation_error": validation["error"],
        "confidence": result.confidence,
        "message": result.message,
        "inference_time_ms": result.inference_time_ms,
        "model_name": result.model_name,
        "model_version": result.model_version,
        "device": result.device,
        "decision": result.decision,
        "consensus": raw.get("consensus"),
        "image_quality": input_quality,
    }


def _select_attempt(attempts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    valid = [attempt for attempt in attempts if attempt.get("status") == "success" and attempt.get("valid_smiles")]
    if not valid:
        return None

    def score(attempt: Mapping[str, Any]) -> tuple[int, int, float, int]:
        retry_reasons = set(str(item) for item in attempt.get("retry_reason_codes") or [])
        confidence = attempt.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence_value = 0.0
        attempt_index = int(attempt.get("attempt_index") or 99)
        return (
            0 if any(item.startswith("image_quality_") for item in retry_reasons) else 1,
            0 if "low_confidence" in retry_reasons else 1,
            confidence_value,
            -attempt_index,
        )

    return sorted(valid, key=score, reverse=True)[0]


def _strategy_agreement(attempts: Sequence[Mapping[str, Any]]) -> bool | None:
    valid_canonical = [
        str(attempt.get("canonical_smiles"))
        for attempt in attempts
        if attempt.get("status") == "success" and attempt.get("valid_smiles") and attempt.get("canonical_smiles")
    ]
    if len(valid_canonical) < 2:
        return None
    return len(set(valid_canonical)) == 1
