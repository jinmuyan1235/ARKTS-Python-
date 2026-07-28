"""Leakage-safe feature contract for an optional development-only OCSR router."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


FORBIDDEN_ROUTER_FEATURE_TOKENS = frozenset({
    "ground_truth", "truth", "target", "label", "expected", "test_correct",
    "inchikey_exact", "canonical_exact", "formula_match",
})


@dataclass(frozen=True)
class CandidateRouterFeatures:
    """Features available before a prediction is compared with trusted truth."""

    molscribe_success: bool
    decimer_success: bool
    molscribe_confidence: float | None
    decimer_confidence: float | None
    image_variant: str
    image_quality_score: float | None
    outputs_agree: bool
    molscribe_output_valid: bool
    decimer_output_valid: bool
    fragment_risk: bool
    charge_risk: bool
    molscribe_latency_ms: float | None
    decimer_latency_ms: float | None


def validate_router_feature_names(names: list[str] | tuple[str, ...] | set[str]) -> None:
    forbidden = sorted(
        name for name in names
        if any(token in str(name).strip().lower() for token in FORBIDDEN_ROUTER_FEATURE_TOKENS)
    )
    if forbidden:
        raise ValueError("Candidate router cannot read evaluation-only or ground-truth features: " + ", ".join(forbidden))


def build_router_features(values: Mapping[str, Any]) -> CandidateRouterFeatures:
    """Validate the mapping before constructing the immutable router input."""
    validate_router_feature_names(set(values))
    allowed = {field.name for field in fields(CandidateRouterFeatures)}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError("Unsupported candidate-router features: " + ", ".join(unknown))
    missing = sorted(allowed - set(values))
    if missing:
        raise ValueError("Missing candidate-router features: " + ", ".join(missing))
    return CandidateRouterFeatures(**{key: values[key] for key in allowed})
